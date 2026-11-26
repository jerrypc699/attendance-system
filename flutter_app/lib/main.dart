import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:http/http.dart' as http;
import 'package:mobile_scanner/mobile_scanner.dart';
import 'security_checker.dart';

const String kDefaultApiUrl = String.fromEnvironment(
  'API_URL',
  defaultValue: 'https://walaahr.dpdns.org',
);

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
  String serverUrl = kDefaultApiUrl;
  final TextEditingController _codeController = TextEditingController();
  final TextEditingController _activationController = TextEditingController();

  bool isVerified = false;
  bool isProcessing = false;
  bool isFaceEnrolled = false;
  bool isCameraInitialized = false;

  String employeeCode = "";
  String employeeName = "";
  String employeeDept = "";
  String challengeStatus = "جاهز لتسجيل الحضور";
  String? scannedQrToken;
  DateTime? scannedQrAt;

  CameraController? _controller;

  @override
  void initState() {
    super.initState();
    _loadSavedServerUrl();
    _initCamera();
  }

  Future<void> _loadSavedServerUrl() async {
    final saved = await SecurityChecker.getSavedServerUrl();
    if (saved != null && saved.startsWith("https://")) {
      setState(() => serverUrl = saved);
    }
  }

  Future<void> _initCamera() async {
    if (cameras.isEmpty) return;
    final frontCam = cameras.firstWhere(
      (c) => c.lensDirection == CameraLensDirection.front,
      orElse: () => cameras.first,
    );
    _controller = CameraController(frontCam, ResolutionPreset.medium, enableAudio: false);
    try {
      await _controller!.initialize();
      if (mounted) setState(() => isCameraInitialized = true);
    } catch (_) {}
  }

  @override
  void dispose() {
    _controller?.dispose();
    _codeController.dispose();
    _activationController.dispose();
    super.dispose();
  }

  Future<String?> _captureFrameBase64() async {
    if (_controller == null || !_controller!.value.isInitialized) return null;
    final xfile = await _controller!.takePicture();
    final bytes = await xfile.readAsBytes();
    return "data:image/jpeg;base64,${base64Encode(bytes)}";
  }

  void _openServerConfigDialog() {
    final ctrl = TextEditingController(text: serverUrl);
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("⚙️ إعداد رابط السيرفر (HTTPS)", textDirection: TextDirection.rtl),
        content: TextField(
          controller: ctrl,
          decoration: const InputDecoration(
            hintText: "https://attendance.yourcompany.com",
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("إلغاء")),
          ElevatedButton(
            onPressed: () async {
              final url = ctrl.text.trim().replaceAll(RegExp(r'/+$'), '');
              if (!url.startsWith("https://")) {
                _showDialog("تنبيه", "يجب أن يبدأ رابط السيرفر بـ https:// لحماية البيانات.");
                return;
              }
              await SecurityChecker.saveServerUrl(url);
              if (mounted) setState(() => serverUrl = url);
              if (ctx.mounted) Navigator.pop(ctx);
            },
            child: const Text("حفظ"),
          ),
        ],
      ),
    );
  }

  Future<void> _openQrScanner() async {
    final result = await Navigator.push<String>(
      context,
      MaterialPageRoute(builder: (_) => const QrScannerScreen()),
    );
    if (result != null && result.startsWith("ATTQR:")) {
      setState(() {
        scannedQrToken = result.trim();
        scannedQrAt = DateTime.now();
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("✅ تم مسح كود مدخل الشركة بنجاح (سارٍ 20 ثانية)")),
        );
      }
    }
  }

  Future<void> _fetchEmployeeData(String code) async {
    final cleanCode = code.trim();
    if (cleanCode.isEmpty) {
      _showDialog("تنبيه", "يرجى إدخال كود الموظف أولاً.");
      return;
    }

    setState(() {
      isProcessing = true;
      challengeStatus = "جارٍ التحقق من بيانات الموظف...";
    });

    try {
      final res = await http
          .get(Uri.parse('$serverUrl/api/v1/attendance/status/${Uri.encodeComponent(cleanCode)}'))
          .timeout(const Duration(seconds: 15));

      final data = jsonDecode(utf8.decode(res.bodyBytes));
      if (res.statusCode == 200) {
        setState(() {
          isVerified = true;
          employeeCode = data['employee_code'] ?? cleanCode;
          employeeName = data['full_name'] ?? cleanCode;
          employeeDept = data['department'] ?? "العام";
          isFaceEnrolled = data['is_face_enrolled'] ?? false;
          challengeStatus = isFaceEnrolled
              ? "اضغط على زر الحضور أو الانصراف لبدء فحص الوجه والحيوية"
              : "أدخل كود التفعيل الصادر من الإدارة ثم التقط صورة لوجهك";
        });
      } else {
        _showDialog("❌ خطأ", data['detail'] ?? "لم يتم العثور على الموظف.");
      }
    } catch (e) {
      _showDialog("خطأ اتصال", "تعذر الاتصال بالسيرفر ($serverUrl).\nتأكد من الإنترنت أو راجع رابط السيرفر من زر الإعدادات.");
    } finally {
      if (mounted) setState(() => isProcessing = false);
    }
  }

  /// Secure First-Time Activation & Server-Side AI Face Enrollment
  Future<void> _executeFirstTimeEnrollment() async {
    final activationCode = _activationController.text.trim();
    if (activationCode.isEmpty) {
      _showDialog("كود التفعيل مطلوب", "يرجى إدخال كود التفعيل (مثال: ACT-XXXXXX) الصادر من إدارة الموارد البشرية.");
      return;
    }

    setState(() {
      isProcessing = true;
      challengeStatus = "📸 جارٍ التقاط صورة الوجه وإرسالها للذكاء الاصطناعي...";
    });

    try {
      final faceImage = await _captureFrameBase64();
      if (faceImage == null) {
        throw Exception("الكاميرا الأمامية غير جاهزة! يرجى السماح بصلاحية الكاميرا.");
      }

      final fp = await SecurityChecker.getDeviceFingerprint();
      final meta = await SecurityChecker.getDeviceMetadata();

      final res = await http
          .post(
            Uri.parse('$serverUrl/api/v1/attendance/enroll-mobile'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'employee_code': employeeCode,
              'activation_code': activationCode,
              'device_fingerprint': fp,
              'device_model': meta['device_model'],
              'os_name': meta['os_name'],
              'os_version': meta['os_version'],
              'face_image': faceImage,
            }),
          )
          .timeout(const Duration(seconds: 25));

      final data = jsonDecode(utf8.decode(res.bodyBytes));
      if (res.statusCode == 200 && data['success'] == true) {
        if (data['device_token'] != null) {
          await SecurityChecker.saveDeviceToken(employeeCode, data['device_token']);
        }
        setState(() {
          isFaceEnrolled = true;
          challengeStatus = "جاهز لتسجيل الحضور والانصراف";
        });
        _showDialog(
          "🎉 تم تفعيل حسابك بنجاح!",
          "تم حفظ بصمة وجهك بالذكاء الاصطناعي وربط هذا الهاتف بحسابك بنجاح.",
        );
      } else {
        _showDialog("❌ فشل التفعيل", data['detail'] ?? "تعذر تفعيل الحساب.");
      }
    } catch (e) {
      _showDialog("خطأ", e.toString().replaceFirst("Exception: ", ""));
    } finally {
      if (mounted) setState(() => isProcessing = false);
    }
  }

  /// Real Multi-Layer Anti-Cheat Attendance Punch (Server-Side AI + 3-Frame Active Liveness + Anti-Mock GPS)
  Future<void> _executePunch(String punchType) async {
    setState(() {
      isProcessing = true;
      challengeStatus = "1. فحص موقع GPS وسلامة الجهاز...";
    });

    try {
      // 1. Collect real GPS + Anti-Mock + Wi-Fi BSSID + Hardware Fingerprint
      final telemetry = await SecurityChecker.collectTelemetry();
      if (telemetry.isMockLocation) {
        throw Exception("MOCK_GPS_DETECTED: تم رصد برنامج موقع وهمي (Fake GPS)! أغلق تطبيقات التزييف.");
      }
      final deviceToken = await SecurityChecker.getDeviceToken(employeeCode);

      // 2. Request Single-Use Challenge Nonce
      setState(() => challengeStatus = "2. طلب كود التحدي الأمني...");
      final chRes = await http
          .post(
            Uri.parse('$serverUrl/api/v1/attendance/challenge'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'employee_code': employeeCode,
              'device_fingerprint': telemetry.deviceFingerprint,
              'device_token': deviceToken,
            }),
          )
          .timeout(const Duration(seconds: 15));

      final chData = jsonDecode(utf8.decode(chRes.bodyBytes));
      if (chRes.statusCode != 200) {
        throw Exception(chData['detail'] ?? "هذا الهاتف غير مصرح له بتسجيل الحضور لهذا الموظف.");
      }

      final String nonce = chData['nonce'];
      final List<String> actions = List<String>.from(chData['expected_actions']);
      final actionsMap = {
        'BLINK_TWICE': 'ارمش بعينيك مرتين 👀',
        'TURN_HEAD_LEFT': 'حرّك رأسك قليلاً لليسار 👈',
        'TURN_HEAD_RIGHT': 'حرّك رأسك قليلاً لليمين 👉',
        'SMILE_PROMPTLY': 'ابتسم بوضوح 😊',
        'NOD_HEAD': 'أومئ برأسك للأعلى والأسفل ↕️',
      };
      final arabicPrompt = actions.map((a) => actionsMap[a] ?? a).join(' ثم ');

      // 3. Capture 3 Sequential Real Camera Frames for Server-Side AI Liveness & Recognition
      setState(() => challengeStatus = "⚡ نفّذ الآن: $arabicPrompt (1/3)");
      final f0 = await _captureFrameBase64();
      final t0 = DateTime.now().millisecondsSinceEpoch;
      await Future.delayed(const Duration(milliseconds: 900));

      setState(() => challengeStatus = "⚡ نفّذ الآن: $arabicPrompt (2/3)");
      final f1 = await _captureFrameBase64();
      final t1 = DateTime.now().millisecondsSinceEpoch;
      await Future.delayed(const Duration(milliseconds: 900));

      setState(() => challengeStatus = "🤖 جارٍ التحقق من الوجه بالذكاء الاصطناعي (3/3)...");
      final f2 = await _captureFrameBase64();
      final t2 = DateTime.now().millisecondsSinceEpoch;

      final frames = [f0, f1, f2].whereType<String>().toList();
      if (frames.isEmpty) {
        throw Exception("تعذر التقاط صورة من الكاميرا الأمامية.");
      }

      // Include scanned entrance QR if still fresh (< 35s)
      String? validQr;
      if (scannedQrToken != null &&
          scannedQrAt != null &&
          DateTime.now().difference(scannedQrAt!).inSeconds <= 35) {
        validQr = scannedQrToken;
      }

      // 4. Submit Punch to Server-Side AI Pipeline
      final punchRes = await http
          .post(
            Uri.parse('$serverUrl/api/v1/attendance/punch'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'employee_code': employeeCode,
              'punch_type': punchType,
              'nonce': nonce,
              'device_fingerprint': telemetry.deviceFingerprint,
              'device_token': deviceToken,
              'face_snapshot': frames.last,
              'liveness_frames': frames,
              'liveness_proof': {
                'actions_performed': actions,
                'motion_timestamp_ms': [t0, t1, t2],
                'liveness_score': 0.0,
              },
              'latitude': telemetry.latitude,
              'longitude': telemetry.longitude,
              'gps_accuracy_meters': telemetry.accuracy,
              'is_mock_location': telemetry.isMockLocation,
              'wifi_bssid': telemetry.wifiBssid,
              'qr_token': validQr,
              'client_timestamp': DateTime.now().toUtc().toIso8601String(),
            }),
          )
          .timeout(const Duration(seconds: 25));

      final pData = jsonDecode(utf8.decode(punchRes.bodyBytes));
      setState(() {
        scannedQrToken = null;
        scannedQrAt = null;
      });

      if (punchRes.statusCode == 200 && pData['success'] == true) {
        final sim = ((pData['telemetry_details']?['face_similarity'] ?? 0) * 100).toStringAsFixed(1);
        final dist = pData['telemetry_details']?['distance_meters'] ?? '--';
        _showDialog(
          "✅ تم اعتماد ${punchType == 'CHECK_IN' ? 'الحضور' : 'الانصراف'}!",
          "الموظف: ${pData['employee_name']}\nالفرع: ${pData['telemetry_details']?['branch'] ?? '--'}\nالمسافة: $dist م\nتطابق الوجه (AI): $sim%",
        );
      } else {
        _showDialog("❌ تم رفض البصمة", pData['rejection_reason'] ?? pData['detail'] ?? "فشل التحقق الأمني");
      }
    } catch (e) {
      _showDialog("تنبيه", e.toString().replaceFirst("Exception: ", ""));
    } finally {
      if (mounted) {
        setState(() {
          isProcessing = false;
          challengeStatus = "جاهز لتسجيل الحضور والانصراف";
        });
      }
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
          isVerified && employeeName.isNotEmpty ? employeeName : 'الحضور الذكي',
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
        ),
        centerTitle: true,
        backgroundColor: const Color(0xFF1E1B4B),
        foregroundColor: Colors.white,
        leading: isVerified
            ? IconButton(
                icon: const Icon(Icons.arrow_back),
                onPressed: () => setState(() => isVerified = false),
              )
            : null,
        actions: [
          IconButton(
            tooltip: "إعدادات رابط السيرفر",
            icon: const Icon(Icons.settings, color: Colors.white70),
            onPressed: _openServerConfigDialog,
          ),
        ],
      ),
      backgroundColor: const Color(0xFF0F172A),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: !isVerified ? _buildEmployeeLookupView() : _buildPunchReadyView(),
        ),
      ),
    );
  }

  Widget _buildEmployeeLookupView() {
    return Container(
      constraints: const BoxConstraints(maxWidth: 400),
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1B4B).withOpacity(0.65),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: const Color(0xFF3730A3)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Icon(Icons.verified_user_outlined, size: 50, color: Color(0xFF818CF8)),
          const SizedBox(height: 12),
          const Text(
            "إدخال كود الموظف",
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 6),
          const Text(
            "أدخل كودك الوظيفي للتحقق وبدء بصمة الوجه والموقع",
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white60, fontSize: 12),
          ),
          const SizedBox(height: 20),
          TextField(
            controller: _codeController,
            textAlign: TextAlign.center,
            textCapitalization: TextCapitalization.characters,
            style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold),
            decoration: InputDecoration(
              hintText: "مثال: EMP-2001",
              hintStyle: const TextStyle(color: Colors.white38, fontSize: 14),
              filled: true,
              fillColor: const Color(0xFF0F172A),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(16)),
            ),
            onSubmitted: _fetchEmployeeData,
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
                : const Icon(Icons.login),
            label: const Text("متابعة", style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  Widget _buildPunchReadyView() {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Container(
          constraints: const BoxConstraints(maxWidth: 400),
          margin: const EdgeInsets.only(bottom: 16),
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: const Color(0xFF1E1B4B).withOpacity(0.7),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: const Color(0xFF4338CA)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(employeeName, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15)),
                  Text("$employeeCode • $employeeDept", style: const TextStyle(color: Colors.white70, fontSize: 12)),
                ],
              ),
              Text(
                isFaceEnrolled ? "🟢 مفعّل (AI)" : "⚠️ بحاجة لتفعيل",
                style: TextStyle(color: isFaceEnrolled ? Colors.greenAccent : Colors.amberAccent, fontWeight: FontWeight.bold, fontSize: 12),
              ),
            ],
          ),
        ),

        Container(
          width: 230,
          height: 230,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(color: isFaceEnrolled ? const Color(0xFF6366F1) : const Color(0xFFF59E0B), width: 4),
          ),
          clipBehavior: Clip.antiAlias,
          child: isCameraInitialized ? CameraPreview(_controller!) : const Center(child: CircularProgressIndicator()),
        ),
        const SizedBox(height: 14),

        Text(
          challengeStatus,
          textAlign: TextAlign.center,
          style: TextStyle(color: isFaceEnrolled ? Colors.white : Colors.amberAccent, fontSize: 13, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 16),

        if (!isFaceEnrolled)
          Container(
            constraints: const BoxConstraints(maxWidth: 340),
            child: Column(
              children: [
                TextField(
                  controller: _activationController,
                  textAlign: TextAlign.center,
                  textCapitalization: TextCapitalization.characters,
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
                  decoration: InputDecoration(
                    hintText: "كود التفعيل من الإدارة (ACT-XXXXXX)",
                    hintStyle: const TextStyle(color: Colors.white38, fontSize: 13),
                    filled: true,
                    fillColor: const Color(0xFF1E1B4B),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                  ),
                ),
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFFD97706),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                    ),
                    onPressed: isProcessing ? null : _executeFirstTimeEnrollment,
                    icon: const Icon(Icons.camera_alt),
                    label: const Text("📸 تفعيل الحساب وبصمة الوجه الآن", style: TextStyle(fontWeight: FontWeight.bold)),
                  ),
                ),
              ],
            ),
          )
        else
          Column(
            children: [
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: scannedQrToken != null ? Colors.greenAccent : Colors.white70,
                  side: BorderSide(color: scannedQrToken != null ? Colors.greenAccent : Colors.white24),
                ),
                onPressed: isProcessing ? null : _openQrScanner,
                icon: const Icon(Icons.qr_code_scanner, size: 18),
                label: Text(scannedQrToken != null ? "✅ تم مسح QR المدخل" : "مسح QR مدخل الشركة (اختياري/حسب الفرع)"),
              ),
              const SizedBox(height: 12),
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
                      label: const Text("حضور", style: TextStyle(fontWeight: FontWeight.bold)),
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
                      label: const Text("انصراف", style: TextStyle(fontWeight: FontWeight.bold)),
                    ),
                  ),
                ],
              ),
            ],
          ),
      ],
    );
  }
}

class QrScannerScreen extends StatelessWidget {
  const QrScannerScreen({super.key});

  @override
  Widget build(BuildContext context) {
    bool returned = false;
    return Scaffold(
      appBar: AppBar(title: const Text("مسح QR مدخل الشركة"), backgroundColor: const Color(0xFF1E1B4B), foregroundColor: Colors.white),
      body: MobileScanner(
        onDetect: (capture) {
          if (returned) return;
          for (final barcode in capture.barcodes) {
            final raw = barcode.rawValue;
            if (raw != null && raw.startsWith("ATTQR:")) {
              returned = true;
              Navigator.pop(context, raw);
              break;
            }
          }
        },
      ),
    );
  }
}
