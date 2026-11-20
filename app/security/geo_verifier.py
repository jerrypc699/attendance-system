import math
from typing import Tuple, List, Optional
from app.config import settings

def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees) using Haversine formula.
    """
    R = 6371000.0 # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return R * c

class GeoVerifier:
    @staticmethod
    def verify_location(
        user_lat: float,
        user_lng: float,
        gps_accuracy: float,
        is_mock_location: bool,
        branch_lat: float,
        branch_lng: float,
        geofence_radius: int,
        client_wifi_bssid: Optional[str] = None,
        branch_allowed_bssids: Optional[List[str]] = None,
        client_beacon_uuid: Optional[str] = None,
        branch_allowed_beacons: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[str], float, bool, bool]:
        """
        Multi-factor physical location validation.
        Returns: (is_valid, failure_reason, distance_meters, wifi_matched, beacon_matched)
        """
        # 1. Anti-Mock / Fake GPS check
        if is_mock_location:
            return False, "MOCK_GPS_DETECTED: Phone is using simulated or mock location provider.", 0.0, False, False

        # 2. GPS Accuracy threshold (reject low precision / triangulated cellular drifts)
        if gps_accuracy > settings.MAX_GPS_ACCURACY_THRESHOLD_METERS:
            return False, f"LOW_GPS_ACCURACY: Reported accuracy {gps_accuracy}m exceeds maximum allowed threshold of {settings.MAX_GPS_ACCURACY_THRESHOLD_METERS}m.", 0.0, False, False

        # 3. Geofence Distance Calculation
        distance = haversine_distance_meters(user_lat, user_lng, branch_lat, branch_lng)
        
        # Effective allowed distance = geofence radius + acceptable GPS tolerance (e.g. 10m)
        effective_allowed_distance = geofence_radius + 10.0
        
        # 4. Check Wi-Fi BSSID Match
        wifi_matched = False
        branch_allowed_bssids = branch_allowed_bssids or []
        if client_wifi_bssid and branch_allowed_bssids:
            clean_client_bssid = client_wifi_bssid.strip().lower()
            wifi_matched = any(clean_client_bssid == allowed.strip().lower() for allowed in branch_allowed_bssids)

        # 5. Check BLE Beacon Match
        beacon_matched = False
        branch_allowed_beacons = branch_allowed_beacons or []
        if client_beacon_uuid and branch_allowed_beacons:
            clean_client_beacon = client_beacon_uuid.strip().lower()
            beacon_matched = any(clean_client_beacon == allowed.strip().lower() for allowed in branch_allowed_beacons)

        # 6. Physical Anchor Verification Policy:
        # If the branch has physical anchors (Wi-Fi / Beacon) configured, and require_anchor is active:
        has_anchors_configured = bool(branch_allowed_bssids or branch_allowed_beacons)
        
        if distance > effective_allowed_distance:
            # If outside GPS geofence, strictly reject
            return False, f"GEOFENCE_EXCEEDED: Distance is {round(distance, 1)}m, which exceeds allowed radius of {geofence_radius}m.", distance, wifi_matched, beacon_matched

        if has_anchors_configured and settings.REQUIRE_WIFI_OR_BEACON:
            if not (wifi_matched or beacon_matched):
                return False, "PHYSICAL_ANCHOR_MISMATCH: Device is inside GPS radius but not connected to Office Wi-Fi or BLE Beacon.", distance, wifi_matched, beacon_matched

        return True, None, round(distance, 2), wifi_matched, beacon_matched

