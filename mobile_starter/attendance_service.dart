// ==============================================================================
// Flutter Attendance API Service
// Communicates securely with the Anti-Cheat Backend
// ==============================================================================

import 'dart:convert';
import 'package:http/http.dart' as http;
import 'security_checker.dart';

class ChallengeResponse {
  final String nonce;
  final List<String> expectedActions;
  final int expiresInSeconds;

  ChallengeResponse({
    required this.nonce,
    required this.expectedActions,
    required this.expiresInSeconds,
  });

  factory ChallengeResponse.fromJson(Map<String, dynamic> json) {
    return ChallengeResponse(
      nonce: json['nonce'],
      expectedActions: List<String>.from(json['expected_actions']),
      expiresInSeconds: json['expires_in_seconds'],
    );
  }
}

class PunchResult {
  final bool success;
  final String status;
  final String message;
  final String? rejectionReason;
  final String? punchId;

  PunchResult({
    required this.success,
    required this.status,
    required this.message,
    this.rejectionReason,
    this.punchId,
  });

  factory PunchResult.fromJson(Map<String, dynamic> json) {
    return PunchResult(
      success: json['success'] ?? false,
      status: json['status'] ?? 'REJECTED',
      message: json['message'] ?? '',
      rejectionReason: json['rejection_reason'],
      punchId: json['punch_id'],
    );
  }
}

class AttendanceService {
  final String baseUrl;

  AttendanceService({required this.baseUrl});

  /// Step 1: Request active liveness challenge token
  Future<ChallengeResponse> requestChallenge({
    required String employeeCode,
    required String deviceFingerprint,
  }) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/attendance/challenge'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'employee_code': employeeCode,
        'device_fingerprint': deviceFingerprint,
      }),
    );

    if (response.statusCode == 200) {
      return ChallengeResponse.fromJson(jsonDecode(response.body));
    } else {
      final error = jsonDecode(response.body);
      throw Exception(error['detail'] ?? 'Failed to get security challenge.');
    }
  }

  /// Step 2: Execute punch with biometric embedding and verified telemetries
  Future<PunchResult> punchAttendance({
    required String employeeCode,
    required String punchType, // 'CHECK_IN' or 'CHECK_OUT'
    required String nonce,
    required List<double> faceEmbedding,
    required List<String> performedActions,
    required double livenessScore,
    required DeviceTelemetry telemetry,
    String? beaconUuid,
  }) async {
    final payload = {
      'employee_code': employeeCode,
      'punch_type': punchType,
      'nonce': nonce,
      'device_fingerprint': telemetry.deviceFingerprint,
      'face_embedding': faceEmbedding,
      'liveness_proof': {
        'actions_performed': performedActions,
        'motion_timestamp_ms': [DateTime.now().millisecondsSinceEpoch],
        'liveness_score': livenessScore,
      },
      'latitude': telemetry.latitude,
      'longitude': telemetry.longitude,
      'gps_accuracy_meters': telemetry.accuracy,
      'is_mock_location': telemetry.isMockLocation,
      'wifi_bssid': telemetry.wifiBssid,
      'beacon_uuid': beaconUuid,
      'client_timestamp': DateTime.now().toUtc().toIso8601String(),
    };

    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/attendance/punch'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(payload),
    );

    if (response.statusCode == 200) {
      return PunchResult.fromJson(jsonDecode(response.body));
    } else {
      final error = jsonDecode(response.body);
      throw Exception(error['detail'] ?? 'Attendance verification failed.');
    }
  }
}

