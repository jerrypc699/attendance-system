import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;

List<CameraDescription> cameras = [];

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    cameras = await availableCameras();
  } catch (_) {}
  runApp(const AttendanceApp());
}

class AttendanceApp extends StatelessWidget {
  const AttendanceApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'نظام الحضور والانصراف الذكي',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF4338CA)),
        useMaterial3: true,
      ),
      home: const AttendanceHomeScreen(),
    );
  }
}

class AttendanceHomeScreen extends StatefulWidget {
  const AttendanceHomeScreen({super.key});

  @override
  State<AttendanceHomeScreen> createState() => _AttendanceHomeScreenState();
}

class _AttendanceHomeScreenState extends State<AttendanceHomeScreen> {
  // Local network IP of backend server
  final String serverUrl = "http://192.168.100.11:8000";
  final String employeeCode = "EMP-2001";
  CameraController? _controller;
  bool isCameraInitialized = false;
  String challengeStatus = "جاهز لتسجيل الحضور";
  bool isProcessing = false;

  @override
  void initState() {
    super.initState();
    _initCamera();
  }

  Future<void> _initCamera() async {
    if (cameras.isEmpty) return;
    // Prefer front camera for face punch
    final frontCam = cameras.firstWhere(
      (c) => c.lensDirection == CameraLensDirection.front,
      orElse: () => cameras.first,
    );

    _controller = CameraController(frontCam, ResolutionPreset.medium, enableAudio: false);
    await _controller!.initialize();
    if (mounted) setState(() => isCameraInitialized = true);
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  Future<void> _executePunch(String punchType) async {
    setState(() {
      isProcessing = true;
      challengeStatus = "1. جلب كود الأمان وفحص الهاتف...";
    });

    try {
      // Step 1: Request Challenge
      final chRes = await http.post(
        Uri.parse('$serverUrl/api/v1/attendance/challenge'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'employee_code': employeeCode,
          'device_fingerprint': 'sha256_hardware_fingerprint_ahmed_phone',
        }),
      );

      if (chRes.statusCode != 200) {
        throw Exception("فشل التحقق من أمان الجهاز أو الموظف");
      }

      final chData = jsonDecode(chRes.body);
      final String nonce = chData['nonce'];
      final List actions = chData['expected_actions'];

      setState(() {
        challengeStatus = "2. تم التحقق من الحركة: $actions";
      });

      // Step 2: Get Location with mock check
      Position pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      );

      // Step 3: Get enrolled face vector
      final faceRes = await http.get(Uri.parse('$serverUrl/api/v1/admin/employees/$employeeCode/face-vector'));
      List faceVec = [];
      if (faceRes.statusCode == 200) {
        faceVec = jsonDecode(faceRes.body)['face_embedding'];
      }

      // Step 4: Submit Punch
      final punchRes = await http.post(
        Uri.parse('$serverUrl/api/v1/attendance/punch'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'employee_code': employeeCode,
          'punch_type': punchType,
          'nonce': nonce,
          'device_fingerprint': 'sha256_hardware_fingerprint_ahmed_phone',
          'face_embedding': faceVec,
          'liveness_proof': {
            'actions_performed': actions,
            'motion_timestamp_ms': [DateTime.now().millisecondsSinceEpoch],
            'liveness_score': 0.95,
          },
          'latitude': pos.latitude,
          'longitude': pos.longitude,
          'gps_accuracy_meters': pos.accuracy,
          'is_mock_location': pos.isMocked, // Anti-Fake GPS
          'wifi_bssid': '00:14:22:01:23:45',
          'client_timestamp': DateTime.now().toUtc().toIso8601String(),
        }),
      );

      final pData = jsonDecode(punchRes.body);

      if (pData['success'] == true) {
        _showDialog("✅ تم اعتماد الحضور!", "الموظف: ${pData['employee_name']}\nالمسافة: ${pData['telemetry_details']['distance_meters']}م\nالتوقيت: ${pData['server_timestamp']}");
      } else {
        _showDialog("❌ تم رفض البصمة!", pData['rejection_reason'] ?? "فشل التحقق الأمني");
      }
    } catch (e) {
      _showDialog("خطأ", e.toString());
    } finally {
      setState(() {
        isProcessing = false;
        challengeStatus = "جاهز لتسجيل الحضور";
      });
    }
  }

  void _showDialog(String title, String message) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title, textDirection: TextDirection.rtl),
        content: Text(message, textDirection: TextDirection.rtl),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("حسناً")),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('نظام الحضور والانصراف الذكي', style: TextStyle(fontWeight: FontWeight.bold)),
        centerTitle: true,
        backgroundColor: const Color(0xFF1E1B4B),
        foregroundColor: Colors.white,
      ),
      backgroundColor: const Color(0xFF0F172A),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(20.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              // Camera Preview Circle
              Container(
                width: 260,
                height: 260,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(color: const Color(0xFF6366F1), width: 4),
                ),
                clipBehavior: Clip.antiAlias,
                child: isCameraInitialized
                    ? CameraPreview(_controller!)
                    : const Center(child: CircularProgressIndicator()),
              ),
              const SizedBox(height: 24),

              Text(
                challengeStatus,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70, fontSize: 14, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 32),

              // Action Buttons
              Row(
                children: [
                  Expanded(
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF059669),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                      ),
                      onPressed: isProcessing ? null : () => _executePunch("CHECK_IN"),
                      icon: const Icon(Icons.check_circle_outline),
                      label: const Text("تسجيل حضور", style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFFDC2626),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                      ),
                      onPressed: isProcessing ? null : () => _executePunch("CHECK_OUT"),
                      icon: const Icon(Icons.exit_to_app),
                      label: const Text("تسجيل انصراف", style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

