import 'dart:convert';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:geolocator/geolocator.dart';
import 'package:network_info_plus/network_info_plus.dart';

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
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static const _fpKey = "hw_device_fingerprint_v2";
  static const _serverUrlKey = "configured_server_url";

  static Future<String?> getSavedServerUrl() async {
    return await _storage.read(key: _serverUrlKey);
  }

  static Future<void> saveServerUrl(String url) async {
    await _storage.write(key: _serverUrlKey, value: url.trim().replaceAll(RegExp(r'/+$'), ''));
  }

  static Future<String?> getDeviceToken(String employeeCode) async {
    return await _storage.read(key: "device_token_${employeeCode.toUpperCase()}");
  }

  static Future<void> saveDeviceToken(String employeeCode, String token) async {
    await _storage.write(key: "device_token_${employeeCode.toUpperCase()}", value: token);
  }

  /// Persistent hardware-derived SHA-256 fingerprint (never derived from employeeCode!)
  static Future<String> getDeviceFingerprint() async {
    final existing = await _storage.read(key: _fpKey);
    if (existing != null && existing.isNotEmpty) {
      return existing;
    }

    final deviceInfo = DeviceInfoPlugin();
    String rawSeed = "";
    if (Platform.isAndroid) {
      final android = await deviceInfo.androidInfo;
      rawSeed = "${android.id}_${android.hardware}_${android.model}_${android.brand}_${android.fingerprint}";
    } else if (Platform.isIOS) {
      final ios = await deviceInfo.iosInfo;
      rawSeed = "${ios.identifierForVendor}_${ios.utsname.machine}_${ios.model}";
    } else {
      rawSeed = "fallback_${DateTime.now().microsecondsSinceEpoch}";
    }

    final hash = sha256.convert(utf8.encode(rawSeed)).toString();
    await _storage.write(key: _fpKey, value: hash);
    return hash;
  }

  static Future<Map<String, String>> getDeviceMetadata() async {
    final deviceInfo = DeviceInfoPlugin();
    if (Platform.isAndroid) {
      final android = await deviceInfo.androidInfo;
      return {
        "os_name": "android",
        "os_version": android.version.release,
        "device_model": "${android.manufacturer} ${android.model}",
      };
    } else if (Platform.isIOS) {
      final ios = await deviceInfo.iosInfo;
      return {
        "os_name": "ios",
        "os_version": ios.systemVersion,
        "device_model": ios.model,
      };
    }
    return {"os_name": "mobile", "os_version": "1.0", "device_model": "Mobile Device"};
  }

  /// Requests location permission cleanly and collects genuine GPS + Anti-Mock + Wi-Fi BSSID
  static Future<DeviceTelemetry> collectTelemetry() async {
    final serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) {
      throw Exception("خدمة الموقع (GPS) مغلقة! يرجى تفعيل الموقع من إعدادات الهاتف.");
    }

    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
      if (permission == LocationPermission.denied) {
        throw Exception("تم رفض صلاحية الموقع! يجب السماح بالموقع لتسجيل الحضور.");
      }
    }
    if (permission == LocationPermission.deniedForever) {
      throw Exception("صلاحية الموقع مرفوضة نهائياً. افتح إعدادات التطبيق وفعّل الموقع.");
    }

    final position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        timeLimit: Duration(seconds: 15),
      ),
    );

    String? bssid;
    try {
      final info = NetworkInfo();
      final rawBssid = await info.getWifiBSSID();
      if (rawBssid != null && rawBssid != "02:00:00:00:00:00") {
        bssid = rawBssid;
      }
    } catch (_) {
      bssid = null;
    }

    final meta = await getDeviceMetadata();
    final fp = await getDeviceFingerprint();

    return DeviceTelemetry(
      latitude: position.latitude,
      longitude: position.longitude,
      accuracy: position.accuracy,
      isMockLocation: position.isMocked,
      wifiBssid: bssid,
      deviceFingerprint: fp,
      osName: meta["os_name"]!,
      osVersion: meta["os_version"]!,
      deviceModel: meta["device_model"]!,
    );
  }
}

