// ==============================================================================
// Flutter Security & Anti-Cheat Checker
// Required packages in pubspec.yaml:
//   geolocator: ^12.0.0
//   network_info_plus: ^5.0.0
//   flutter_blue_plus: ^1.30.0
//   device_info_plus: ^10.0.0
//   flutter_secure_storage: ^9.0.0
//   freerasp: ^6.0.0 (or flutter_jailbreak_detection)
// ==============================================================================

import 'dart:io';
import 'package:geolocator/geolocator.dart';
import 'package:network_info_plus/network_info_plus.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:crypto/crypto.dart';
import 'dart:convert';

class DeviceTelemetry {
  final double latitude;
  final double longitude;
  final double accuracy;
  final bool isMockLocation;
  final String? wifiBssid;
  final String deviceFingerprint;
  final String osName;
  final String osVersion;
  final String deviceModel;

  DeviceTelemetry({
    required this.latitude,
    required this.longitude,
    required this.accuracy,
    required this.isMockLocation,
    this.wifiBssid,
    required this.deviceFingerprint,
    required this.osName,
    required this.osVersion,
    required this.deviceModel,
  });
}

class SecurityChecker {
  static const _storage = FlutterSecureStorage();

  /// Retrieve persistent hardware-backed device fingerprint
  static Future<String> getDeviceFingerprint() async {
    String? storedId = await _storage.read(key: "hw_device_fingerprint");
    if (storedId != null && storedId.isNotEmpty) {
      return storedId;
    }

    final deviceInfo = DeviceInfoPlugin();
    String rawSeed = "";

    if (Platform.isAndroid) {
      final android = await deviceInfo.androidInfo;
      rawSeed = "${android.id}_${android.hardware}_${android.model}_${android.brand}";
    } else if (Platform.isIOS) {
      final ios = await deviceInfo.iosInfo;
      rawSeed = "${ios.identifierForVendor}_${ios.model}_${ios.systemVersion}";
    }

    // Generate SHA-256 fingerprint
    final hash = sha256.convert(utf8.encode(rawSeed)).toString();
    await _storage.write(key: "hw_device_fingerprint", value: hash);
    return hash;
  }

  /// Collect verified location and physical anchor telemetry
  static Future<DeviceTelemetry> collectTelemetry() async {
    // 1. Permission check
    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
      if (permission == LocationPermission.denied) {
        throw Exception("LOCATION_PERMISSION_DENIED");
      }
    }

    // 2. High accuracy position with isMock check
    Position position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        timeLimit: Duration(seconds: 15),
      ),
    );

    // CRITICAL ANTI-CHEAT: Check if position is from Mock/Fake GPS Provider
    bool isMock = position.isMocked;

    // 3. Wi-Fi BSSID (MAC Address of office router)
    String? bssid;
    try {
      final info = NetworkInfo();
      bssid = await info.getWifiBSSID();
    } catch (_) {
      bssid = null;
    }

    // 4. Device info
    final deviceInfo = DeviceInfoPlugin();
    String osName = Platform.isAndroid ? "android" : "ios";
    String osVersion = "";
    String model = "";

    if (Platform.isAndroid) {
      final android = await deviceInfo.androidInfo;
      osVersion = android.version.release;
      model = "${android.manufacturer} ${android.model}";
    } else {
      final ios = await deviceInfo.iosInfo;
      osVersion = ios.systemVersion;
      model = ios.model;
    }

    final fingerprint = await getDeviceFingerprint();

    return DeviceTelemetry(
      latitude: position.latitude,
      longitude: position.longitude,
      accuracy: position.accuracy,
      isMockLocation: isMock,
      wifiBssid: bssid,
      deviceFingerprint: fingerprint,
      osName: osName,
      osVersion: osVersion,
      deviceModel: model,
    );
  }
}

