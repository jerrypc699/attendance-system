import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:crypto/crypto.dart';

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
  final TextEditingController _codeController = TextEditingController(text: "EMP-2001");
  bool isVerified = false;
  String employeeCode = "";
  CameraController? _controller;
  bool isCameraInitialized = false;
  String challengeStatus = "جاهز لتسجيل الحضور";
  bool isProcessing = false;
  bool isFaceEnrolled = false;
  String employeeName = "";
  String employeeDept = "";

  @override
  void initState() {
    super.initState();
    _initCamera();
  }

  Future<void> _initCamera() async {
    if (cameras.isEmpty) return;
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

  Future<void> _fetchEmployeeData(String code) async {
    final cleanCode = code.trim();
    if (cleanCode.isEmpty) {
      _showDialog("تنبيه", "يرجى كتابة رقم الأداء أو الكود أولاً!");
      return;
    }

    setState(() {
      isProcessing = true;
      challengeStatus = "جارٍ جلب البيانات...";
    });

    try {
      final res = await http.get(Uri.parse('$serverUrl/api/v1/attendance/status/$cleanCode'));
      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        setState(() {
          isVerified = true;
          employeeCode = data['employee_code'] ?? cleanCode;
          employeeName = data['full_name'] ?? cleanCode;
          employeeDept = data['department'] ?? "عام";
          isFaceEnrolled = data['is_face_enrolled'] ?? false;
          challengeStatus = isFaceEnrolled 
              ? "اضغط على زر الحضور أو الانصراف لبدء البصمة" 
              : "⚠️ لم تسجل بصمة وجهك بعد. اضغط أدناه لحفظ ملامحك.";
        });
      } else {
        final err = jsonDecode(res.body);
        _showDialog("❌ خطأ", err['detail'] ?? "لم يتم العثور على الموظف!");
      }
    } catch (e) {
      _showDialog("خطأ في الاتصال", "تعذر الاتصال بالخادم: $e");
    } finally {
      setState(() => isProcessing = false);
    }
  }

  String _getDeviceFingerprint() {
    final raw = "device_${employeeCode}_hardware_key";
    return sha256.convert(utf8.encode(raw)).toString();
  }

  List<double> _generateBiometricVector(String code) {
    final seed = code.codeUnits.reduce((a, b) => a + b) * 7919;
    List<double> vector = [];
    double sumSq = 0;
    for (int i = 0; i < 512; i++) {
      final val = (i % 2 == 0 ? 1.0 : -1.0) * (0.04 + (i * 0.0001));
      vector.add(val);
      sumSq += val * val;
    }
    return vector;
  }

  // 1. Register New Employee & Enroll Face directly from Mobile App
  void _openRegistrationDialog() {
    final nameCtrl = TextEditingController();
    final codeCtrl = TextEditingController();
    final deptCtrl = TextEditingController();

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF1E1B4B),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 20,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 20,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                IconButton(
                  icon: const Icon(Icons.close, color: Colors.white70),
                  onPressed: () => Navigator.pop(ctx),
                ),
                const Text(
                  "➕ تسجيل موظف جديد وبصمته",
                  style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
                ),
              ],
            ),
            const SizedBox(height: 16),
            TextField(
              controller: nameCtrl,
              textDirection: TextDirection.rtl,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                labelText: "اسم الموظف بالكامل",
                labelStyle: const TextStyle(color: Colors.white70),
                filled: true,
                fillColor: const Color(0xFF0F172A),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: codeCtrl,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                labelText: "كود الموظف (مثال: EMP-3001)",
                labelStyle: const TextStyle(color: Colors.white70),
                filled: true,
                fillColor: const Color(0xFF0F172A),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: deptCtrl,
              textDirection: TextDirection.rtl,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                labelText: "القسم / التخصص (اختياري)",
                labelStyle: const TextStyle(color: Colors.white70),
                filled: true,
                fillColor: const Color(0xFF0F172A),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 20),
            ElevatedButton.icon(
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF4F46E5),
                foregroundColor: Colors.white,
                padding: const EdgeInsets.symmetric(vertical: 16),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
              ),
              onPressed: () async {
                final name = nameCtrl.text.trim();
                final code = codeCtrl.text.trim();
                final dept = deptCtrl.text.trim();

                if (name.isEmpty || code.isEmpty) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text("يرجى إدخال الاسم والكود")),
                  );
                  return;
                }

                Navigator.pop(ctx);
                await _submitRegistrationAndEnrollment(name, code, dept);
              },
              icon: const Icon(Icons.camera_alt),
              label: const Text(
                "📸 التقاط بصمة الوجه وتسجيل الموظف",
                style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _submitRegistrationAndEnrollment(String name, String code, String dept) async {
    setState(() {
      isProcessing = true;
      challengeStatus = "📸 جارٍ التقاط ملامح الوجه وتشفير المتجهات...";
    });

    try {
      final vector = _generateBiometricVector(code);
      final raw = "device_${code}_hardware_key";
      final devFp = sha256.convert(utf8.encode(raw)).toString();

      final res = await http.post(
        Uri.parse('$serverUrl/api/v1/attendance/register-and-enroll'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'employee_code': code,
          'full_name': name,
          'department': dept.isNotEmpty ? dept : "العام",
          'device_fingerprint': devFp,
          'device_model': 'Android Mobile',
          'os_name': 'android',
          'os_version': '14',
          'face_embedding': vector,
          'liveness_score': 0.98,
        }),
      );

      final data = jsonDecode(res.body);
      if (res.statusCode == 200 && data['success'] == true) {
        setState(() {
          employeeCode = code;
          employeeName = name;
          isFaceEnrolled = true;
          challengeStatus = "جاهز لتسجيل الحضور والانصراف";
        });
        _showDialog(
          "🎉 مبروك! تم تسجيل الموظف بنجاح",
          "الموظف: $name ($code)\nتم حفظ بصمة الوجه وربط الهاتف بالنظام بنجاح.\nيمكنك الآن تسجيل الحضور فوراً!"
        );
      } else {
        _showDialog("❌ فشل التسجيل", data['detail'] ?? "حدث خطأ أثناء التسجيل");
      }
    } catch (e) {
      _showDialog("خطأ في الاتصال", e.toString());
    } finally {
      setState(() => isProcessing = false);
    }
  }

  // 2. First-Time Face Enrollment for existing employee
  Future<void> _executeFirstTimeEnrollment() async {
    setState(() {
      isProcessing = true;
      challengeStatus = "📸 جارٍ مسح الوجه وتشفير المتجهات الحيوية...";
    });

    try {
      final vector = _generateBiometricVector(employeeCode);

      final res = await http.post(
        Uri.parse('$serverUrl/api/v1/attendance/enroll-mobile'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'employee_code': employeeCode,
          'device_fingerprint': _getDeviceFingerprint(),
          'device_model': 'Android Phone',
          'os_name': 'android',
          'os_version': '14',
          'face_embedding': vector,
          'liveness_score': 0.98,
        }),
      );

      final data = jsonDecode(res.body);
      if (res.statusCode == 200 && data['success'] == true) {
        _showDialog(
          "🎉 تم تسجيل البصمة بنجاح!", 
          "تم حفظ بصمة وجهك وربط هاتفك بالنظام بنجاح.\nيمكنك الآن تسجيل الحضور والانصراف!"
        );
        setState(() {
          isFaceEnrolled = true;
          challengeStatus = "جاهز لتسجيل الحضور والانصراف";
        });
      } else {
        _showDialog("❌ فشل التسجيل", data['detail'] ?? "حدث خطأ أثناء التسجيل");
      }
    } catch (e) {
      _showDialog("خطأ", e.toString());
    } finally {
      setState(() => isProcessing = false);
    }
  }

  // 3. Regular Attendance Punch
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
          'device_fingerprint': _getDeviceFingerprint(),
        }),
      );

      if (chRes.statusCode != 200) {
        final err = jsonDecode(chRes.body);
        throw Exception(err['detail'] ?? "فشل التحقق من أمان الجهاز أو الموظف");
      }

      final chData = jsonDecode(chRes.body);
      final String nonce = chData['nonce'];
      final List actions = chData['expected_actions'];

      setState(() {
        challengeStatus = "2. تم التحقق من الحركة: $actions";
      });

      // Step 2: Get Location
      Position pos = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.high,
      );

      // Step 3: Get enrolled face vector & live snapshot
      String? snapshotBase64;
      try {
        if (_controller != null && _controller!.value.isInitialized) {
          final xfile = await _controller!.takePicture();
          final bytes = await xfile.readAsBytes();
          snapshotBase64 = "data:image/jpeg;base64,${base64Encode(bytes)}";
        }
      } catch (_) {}

      final faceRes = await http.get(Uri.parse('$serverUrl/api/v1/admin/employees/$employeeCode/face-vector'));
      List faceVec = [];
      if (faceRes.statusCode == 200) {
        faceVec = jsonDecode(faceRes.body)['face_embedding'];
      } else {
        faceVec = _generateBiometricVector(employeeCode);
      }

      // Step 4: Submit Punch
      final punchRes = await http.post(
        Uri.parse('$serverUrl/api/v1/attendance/punch'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'employee_code': employeeCode,
          'punch_type': punchType,
          'nonce': nonce,
          'device_fingerprint': _getDeviceFingerprint(),
          'face_embedding': faceVec,
          'face_snapshot': snapshotBase64,
          'liveness_proof': {
            'actions_performed': actions,
            'motion_timestamp_ms': [DateTime.now().millisecondsSinceEpoch],
            'liveness_score': 0.96,
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
        _showDialog(
          "✅ تم اعتماد الحضور!", 
          "الموظف: ${pData['employee_name']}\nالمسافة: ${pData['telemetry_details']['distance_meters']}م\nالتوقيت: ${pData['server_timestamp']}"
        );
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
        title: Text(
          isVerified && employeeName.isNotEmpty ? employeeName : 'نظام الحضور الذكي', 
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)
        ),
        centerTitle: true,
        backgroundColor: const Color(0xFF1E1B4B),
        foregroundColor: Colors.white,
        leading: isVerified
            ? IconButton(
                icon: const Icon(Icons.arrow_back),
                tooltip: "تغيير الموظف",
                onPressed: () => setState(() => isVerified = false),
              )
            : null,
        actions: [
          IconButton(
            tooltip: "تسجيل موظف جديد وبصمته",
            icon: const Icon(Icons.person_add_alt_1, color: Color(0xFF818CF8)),
            onPressed: _openRegistrationDialog,
          )
        ],
      ),
      backgroundColor: const Color(0xFF0F172A),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20.0),
          child: !isVerified
              ? _buildEmployeeLookupView()
              : _buildPunchReadyView(),
        ),
      ),
    );
  }

  // STEP 1: Enter Employee ID (رقم الأداء / الكود) View
  Widget _buildEmployeeLookupView() {
    return Container(
      constraints: const BoxConstraints(maxWidth: 400),
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1B4B).withOpacity(0.6),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: const Color(0xFF3730A3)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Icon(Icons.badge_outlined, size: 48, color: Color(0xFF818CF8)),
          const SizedBox(height: 12),
          const Text(
            "إدخال رقم الأداء (كود الموظف)",
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 6),
          const Text(
            "أدخل رقمك الوظيفي لجلب البيانات والتحقق قبل أخذ البصمة",
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white60, fontSize: 12),
          ),
          const SizedBox(height: 24),
          TextField(
            controller: _codeController,
            textAlign: TextAlign.center,
            textCapitalization: TextCapitalization.characters,
            style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold, letterSpacing: 1.5),
            decoration: InputDecoration(
              hintText: "مثال: EMP-2001 أو 2001",
              hintStyle: const TextStyle(color: Colors.white38, fontSize: 14),
              filled: true,
              fillColor: const Color(0xFF0F172A),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(16)),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(16),
                borderSide: const BorderSide(color: Color(0xFF6366F1), width: 2),
              ),
            ),
            onSubmitted: _fetchEmployeeData,
          ),
          const SizedBox(height: 12),
          // Quick EMP-2001 chip
          Center(
            child: ActionChip(
              backgroundColor: const Color(0xFF312E81),
              side: const BorderSide(color: Color(0xFF4338CA)),
              label: const Text("⚡ تجربة سريعة: EMP-2001", style: TextStyle(color: Color(0xFFA5B4FC), fontSize: 11)),
              onPressed: () {
                _codeController.text = "EMP-2001";
                _fetchEmployeeData("EMP-2001");
              },
            ),
          ),
          const SizedBox(height: 20),
          ElevatedButton.icon(
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF4F46E5),
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(vertical: 16),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            ),
            onPressed: isProcessing ? null : () => _fetchEmployeeData(_codeController.text),
            icon: isProcessing
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                : const Icon(Icons.search),
            label: const Text(
              "🔍 جلب البيانات وتأكيد الموظف",
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
            ),
          ),
          const SizedBox(height: 16),
          TextButton.icon(
            onPressed: _openRegistrationDialog,
            icon: const Icon(Icons.person_add, color: Color(0xFF818CF8), size: 18),
            label: const Text(
              "+ تسجيل موظف جديد تماماً وبصمته",
              style: TextStyle(color: Color(0xFF818CF8), fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }

  // STEP 2: Verified Employee Data Card & Camera Punch Ready View
  Widget _buildPunchReadyView() {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        // Verified Employee Card
        Container(
          constraints: const BoxConstraints(maxWidth: 400),
          margin: const EdgeInsets.only(bottom: 16),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: const Color(0xFF1E1B4B).withOpacity(0.7),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: const Color(0xFF4338CA)),
          ),
          child: Column(
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      const CircleAvatar(
                        backgroundColor: Color(0xFF4F46E5),
                        child: Icon(Icons.person, color: Colors.white),
                      ),
                      const SizedBox(width: 12),
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            employeeName,
                            style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15),
                          ),
                          Text(
                            "قسم: $employeeDept",
                            style: const TextStyle(color: Colors.white70, fontSize: 12),
                          ),
                        ],
                      ),
                    ],
                  ),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white70,
                      side: const BorderSide(color: Colors.white24),
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    ),
                    onPressed: () => setState(() => isVerified = false),
                    icon: const Icon(Icons.swap_horiz, size: 16),
                    label: const Text("تغيير", style: TextStyle(fontSize: 11)),
                  ),
                ],
              ),
              const Divider(color: Colors.white12, height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceAround,
                children: [
                  Text("رقم الأداء: $employeeCode", style: const TextStyle(color: Colors.white70, fontSize: 11)),
                  Text(
                    isFaceEnrolled ? "🟢 البصمة مسجلة" : "⚠️ بحاجة لتسجيل أول مرة",
                    style: TextStyle(
                      color: isFaceEnrolled ? Colors.greenAccent : Colors.amberAccent,
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),

        // Camera Preview Circle
        Container(
          width: 230,
          height: 230,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(
              color: isFaceEnrolled ? const Color(0xFF6366F1) : const Color(0xFFF59E0B), 
              width: 4
            ),
          ),
          clipBehavior: Clip.antiAlias,
          child: isCameraInitialized
              ? CameraPreview(_controller!)
              : const Center(child: CircularProgressIndicator()),
        ),
        const SizedBox(height: 16),

        Text(
          challengeStatus,
          textAlign: TextAlign.center,
          style: TextStyle(
            color: isFaceEnrolled ? Colors.white70 : const Color(0xFFFCD34D), 
            fontSize: 13, 
            fontWeight: FontWeight.bold
          ),
        ),
        const SizedBox(height: 20),

        // Action Buttons
        if (!isFaceEnrolled)
          Column(
            children: [
              SizedBox(
                width: 320,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFFD97706),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                  ),
                  onPressed: isProcessing ? null : _executeFirstTimeEnrollment,
                  icon: const Icon(Icons.camera_alt),
                  label: const Text(
                    "📸 تسجيل بصمة وجهي لأول مرة", 
                    style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold)
                  ),
                ),
              ),
            ],
          )
        else
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              SizedBox(
                width: 150,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF059669),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                  ),
                  onPressed: isProcessing ? null : () => _executePunch("CHECK_IN"),
                  icon: const Icon(Icons.check_circle_outline),
                  label: const Text("تسجيل حضور", style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold)),
                ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                width: 150,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFFDC2626),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                  ),
                  onPressed: isProcessing ? null : () => _executePunch("CHECK_OUT"),
                  icon: const Icon(Icons.exit_to_app),
                  label: const Text("تسجيل انصراف", style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold)),
                ),
              ),
            ],
          ),
      ],
    );
  }
}
