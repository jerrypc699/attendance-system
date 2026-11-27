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
  bool isAutoChecking = true;
  bool isProcessing = false;
  bool isFaceEnrolled = false;
  bool isCameraInitialized = false;

  String employeeCode = "";
  String employeeName = "";
  String employeeDept = "";
  String challengeStatus = "جاهز لتسجيل الحضور";
  String? scannedQrToken;
  DateTime? scannedQrAt;

  // HR Requests (Leaves, Permissions & Missions) State
  int _selectedTabIndex = 0;
  String? activeMissionLabel;
  bool isLoadingRequests = false;
  double balAnnual = 21.0;
  double balCasual = 6.0;
  double balSick = 15.0;
  List<dynamic> myRequests = [];

  CameraController? _controller;

  @override
  void initState() {
    super.initState();
    _initCamera();
    _bootstrapSession();
  }

  Future<void> _bootstrapSession() async {
    final saved = await SecurityChecker.getSavedServerUrl();
    if (saved != null && saved.startsWith("https://") && mounted) {
      setState(() => serverUrl = saved);
    }
    await _tryAutoRecognizeDevice();
  }

  /// Automatically recognizes an already-bound phone on app launch so the employee
  /// opens directly on the Camera + Punch screen without re-entering their code!
  Future<void> _tryAutoRecognizeDevice() async {
    try {
      final token = await SecurityChecker.getAnySavedDeviceToken();
      if (token == null || token.isEmpty) {
        if (mounted) setState(() => isAutoChecking = false);
        return;
      }
      final fp = await SecurityChecker.getDeviceFingerprint();
      final res = await http
          .post(
            Uri.parse('$serverUrl/api/v1/attendance/device-session'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'device_fingerprint': fp,
              'device_token': token,
            }),
          )
          .timeout(const Duration(seconds: 10));

      if (res.statusCode == 200) {
        final data = jsonDecode(utf8.decode(res.bodyBytes));
        final code = data['employee_code'] ?? "";
        if (code.isNotEmpty) {
          await SecurityChecker.saveDeviceToken(code, token);
        }
        if (mounted) {
          setState(() {
            isVerified = true;
            employeeCode = code;
            employeeName = data['full_name'] ?? code;
            employeeDept = data['department'] ?? "العام";
            isFaceEnrolled = data['is_face_enrolled'] ?? false;
            activeMissionLabel = data['active_mission_today']?['location'];
            final lastPunch = data['last_punch_today'];
            challengeStatus = isFaceEnrolled
                ? (lastPunch == 'CHECK_IN'
                    ? "لقد سجلت حضورك اليوم — جاهز لتسجيل الانصراف"
                    : "جاهز لتسجيل الحضور بالبصمة الحية")
                : "أدخل كود التفعيل الصادر من الإدارة ثم التقط صورة لوجهك";
            isAutoChecking = false;
          });
          if (isFaceEnrolled) _loadMyRequests();
          return;
        }
      }
    } catch (_) {}
    if (mounted) setState(() => isAutoChecking = false);
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
          activeMissionLabel = data['active_mission_today']?['location'];
          challengeStatus = isFaceEnrolled
              ? "اضغط على زر الحضور أو الانصراف لبدء فحص الوجه والحيوية"
              : "أدخل كود التفعيل الصادر من الإدارة ثم التقط صورة لوجهك";
        });
        if (isFaceEnrolled) _loadMyRequests();
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
        _loadMyRequests();
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
        final mission = pData['telemetry_details']?['approved_mission'];
        final missionLine = mission != null ? "\n🚗 المأمورية: $mission" : "";
        _showDialog(
          "✅ تم اعتماد ${punchType == 'CHECK_IN' ? 'الحضور' : 'الانصراف'}!",
          "الموظف: ${pData['employee_name']}\nالفرع: ${pData['telemetry_details']?['branch'] ?? '--'}$missionLine\nالمسافة: $dist م\nتطابق الوجه (AI): $sim%",
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

  // ==================== HR REQUESTS (LEAVES, PERMISSIONS & MISSIONS) ====================
  Future<void> _loadMyRequests() async {
    if (!mounted) return;
    setState(() => isLoadingRequests = true);
    try {
      final fp = await SecurityChecker.getDeviceFingerprint();
      final tok = await SecurityChecker.getDeviceToken(employeeCode) ??
          await SecurityChecker.getAnySavedDeviceToken();
      if (tok == null || tok.isEmpty) {
        if (mounted) setState(() => isLoadingRequests = false);
        return;
      }
      final res = await http
          .post(
            Uri.parse('$serverUrl/api/v1/requests/my-summary'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'device_fingerprint': fp, 'device_token': tok}),
          )
          .timeout(const Duration(seconds: 15));

      if (res.statusCode == 200 && mounted) {
        final data = jsonDecode(utf8.decode(res.bodyBytes));
        final bals = data['balances'] ?? {};
        setState(() {
          balAnnual = (bals['annual'] as num?)?.toDouble() ?? 21.0;
          balCasual = (bals['casual'] as num?)?.toDouble() ?? 6.0;
          balSick = (bals['sick'] as num?)?.toDouble() ?? 15.0;
          myRequests = List<dynamic>.from(data['requests'] ?? []);
        });
      }
    } catch (_) {} finally {
      if (mounted) setState(() => isLoadingRequests = false);
    }
  }

  Future<void> _cancelRequest(String requestId) async {
    try {
      final fp = await SecurityChecker.getDeviceFingerprint();
      final tok = await SecurityChecker.getDeviceToken(employeeCode) ??
          await SecurityChecker.getAnySavedDeviceToken();
      final res = await http
          .post(
            Uri.parse('$serverUrl/api/v1/requests/$requestId/cancel'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'device_fingerprint': fp, 'device_token': tok}),
          )
          .timeout(const Duration(seconds: 15));
      if (res.statusCode == 200) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text("تم إلغاء الطلب بنجاح")),
          );
        }
        await _loadMyRequests();
      }
    } catch (_) {}
  }

  String _fmtDateIso(DateTime d) {
    final y = d.year.toString().padLeft(4, '0');
    final m = d.month.toString().padLeft(2, '0');
    final day = d.day.toString().padLeft(2, '0');
    return '$y-$m-$day';
  }

  String _fmtTimeHHMM(TimeOfDay t) {
    final h = t.hour.toString().padLeft(2, '0');
    final m = t.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }

  void _openNewRequestSheet() {
    const subtypesByCat = <String, List<Map<String, String>>>{
      'LEAVE': [
        {'id': 'ANNUAL', 'label': '🏖️ إجازة اعتيادية'},
        {'id': 'CASUAL', 'label': '⚡ إجازة عارضة'},
        {'id': 'SICK', 'label': '🏥 إجازة مرضية'},
        {'id': 'UNPAID', 'label': '📄 إجازة بدون أجر'},
        {'id': 'OFFICIAL', 'label': '🎖️ إجازة رسمية / خاصة'},
      ],
      'PERMISSION': [
        {'id': 'LATE_ARRIVAL', 'label': '🌅 إذن تأخير صباحي'},
        {'id': 'EARLY_LEAVE', 'label': '🌇 إذن انصراف مبكر'},
        {'id': 'MID_DAY', 'label': '⏱️ إذن خروج أثناء العمل'},
      ],
      'MISSION': [
        {'id': 'EXTERNAL_MISSION', 'label': '🚗 مأمورية عمل خارجية'},
        {'id': 'CLIENT_VISIT', 'label': '🤝 زيارة عميل / موقع'},
        {'id': 'REMOTE_WORK', 'label': '💻 عمل عن بُعد'},
      ],
    };

    String category = 'LEAVE';
    String subtype = 'ANNUAL';
    DateTime startDate = DateTime.now();
    DateTime endDate = DateTime.now();
    TimeOfDay fromTime = const TimeOfDay(hour: 9, minute: 0);
    TimeOfDay toTime = const TimeOfDay(hour: 10, minute: 30);
    final locCtrl = TextEditingController();
    final reasonCtrl = TextEditingController();
    bool submitting = false;
    String? errorMsg;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF0F172A),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      builder: (sheetCtx) => StatefulBuilder(
        builder: (ctx, setSheetState) {
          final subtypes = subtypesByCat[category]!;
          return Directionality(
            textDirection: TextDirection.rtl,
            child: Padding(
              padding: EdgeInsets.only(
                left: 20,
                right: 20,
                top: 20,
                bottom: MediaQuery.of(ctx).viewInsets.bottom + 24,
              ),
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      "📝 تقديم طلب جديد",
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 14),
                    Row(
                      children: [
                        for (final entry in const [
                          ['LEAVE', '🏖️ إجازة'],
                          ['PERMISSION', '⏰ إذن'],
                          ['MISSION', '🚗 مأمورية'],
                        ])
                          Expanded(
                            child: Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 3),
                              child: ChoiceChip(
                                label: Center(child: Text(entry[1], style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold))),
                                selected: category == entry[0],
                                selectedColor: const Color(0xFF4F46E5),
                                backgroundColor: const Color(0xFF1E1B4B),
                                labelStyle: const TextStyle(color: Colors.white),
                                onSelected: (_) {
                                  setSheetState(() {
                                    category = entry[0];
                                    subtype = subtypesByCat[category]!.first['id']!;
                                    errorMsg = null;
                                  });
                                },
                              ),
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    DropdownButtonFormField<String>(
                      value: subtype,
                      dropdownColor: const Color(0xFF1E1B4B),
                      style: const TextStyle(color: Colors.white, fontSize: 13),
                      decoration: InputDecoration(
                        labelText: "نوع الطلب",
                        labelStyle: const TextStyle(color: Colors.white70),
                        filled: true,
                        fillColor: const Color(0xFF1E1B4B),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                      items: subtypes
                          .map((s) => DropdownMenuItem(value: s['id'], child: Text(s['label']!)))
                          .toList(),
                      onChanged: (val) {
                        if (val != null) setSheetState(() => subtype = val);
                      },
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            style: OutlinedButton.styleFrom(
                              foregroundColor: Colors.white,
                              side: const BorderSide(color: Color(0xFF4338CA)),
                              padding: const EdgeInsets.symmetric(vertical: 12),
                            ),
                            icon: const Icon(Icons.calendar_today, size: 16),
                            label: Text(
                              "${category == 'PERMISSION' ? 'التاريخ' : 'من'}: ${_fmtDateIso(startDate)}",
                              style: const TextStyle(fontSize: 12),
                            ),
                            onPressed: () async {
                              final picked = await showDatePicker(
                                context: ctx,
                                initialDate: startDate,
                                firstDate: DateTime(2024),
                                lastDate: DateTime(2030),
                              );
                              if (picked != null) {
                                setSheetState(() {
                                  startDate = picked;
                                  if (endDate.isBefore(startDate)) endDate = startDate;
                                });
                              }
                            },
                          ),
                        ),
                        if (category != 'PERMISSION') ...[
                          const SizedBox(width: 8),
                          Expanded(
                            child: OutlinedButton.icon(
                              style: OutlinedButton.styleFrom(
                                foregroundColor: Colors.white,
                                side: const BorderSide(color: Color(0xFF4338CA)),
                                padding: const EdgeInsets.symmetric(vertical: 12),
                              ),
                              icon: const Icon(Icons.event, size: 16),
                              label: Text("إلى: ${_fmtDateIso(endDate)}", style: const TextStyle(fontSize: 12)),
                              onPressed: () async {
                                final picked = await showDatePicker(
                                  context: ctx,
                                  initialDate: endDate.isBefore(startDate) ? startDate : endDate,
                                  firstDate: startDate,
                                  lastDate: DateTime(2030),
                                );
                                if (picked != null) setSheetState(() => endDate = picked);
                              },
                            ),
                          ),
                        ],
                      ],
                    ),
                    if (category == 'PERMISSION') ...[
                      const SizedBox(height: 10),
                      Row(
                        children: [
                          Expanded(
                            child: OutlinedButton.icon(
                              style: OutlinedButton.styleFrom(
                                foregroundColor: Colors.white,
                                side: const BorderSide(color: Color(0xFF4338CA)),
                                padding: const EdgeInsets.symmetric(vertical: 12),
                              ),
                              icon: const Icon(Icons.access_time, size: 16),
                              label: Text("من: ${_fmtTimeHHMM(fromTime)}", style: const TextStyle(fontSize: 12)),
                              onPressed: () async {
                                final picked = await showTimePicker(context: ctx, initialTime: fromTime);
                                if (picked != null) setSheetState(() => fromTime = picked);
                              },
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: OutlinedButton.icon(
                              style: OutlinedButton.styleFrom(
                                foregroundColor: Colors.white,
                                side: const BorderSide(color: Color(0xFF4338CA)),
                                padding: const EdgeInsets.symmetric(vertical: 12),
                              ),
                              icon: const Icon(Icons.schedule, size: 16),
                              label: Text("إلى: ${_fmtTimeHHMM(toTime)}", style: const TextStyle(fontSize: 12)),
                              onPressed: () async {
                                final picked = await showTimePicker(context: ctx, initialTime: toTime);
                                if (picked != null) setSheetState(() => toTime = picked);
                              },
                            ),
                          ),
                        ],
                      ),
                    ],
                    if (category == 'MISSION') ...[
                      const SizedBox(height: 12),
                      TextField(
                        controller: locCtrl,
                        style: const TextStyle(color: Colors.white, fontSize: 13),
                        decoration: InputDecoration(
                          labelText: "مكان / جهة المأمورية *",
                          hintText: "مثال: مقر العميل / بنك / مصلحة حكومية",
                          hintStyle: const TextStyle(color: Colors.white38, fontSize: 12),
                          labelStyle: const TextStyle(color: Colors.white70),
                          filled: true,
                          fillColor: const Color(0xFF1E1B4B),
                          border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                        ),
                      ),
                    ],
                    const SizedBox(height: 12),
                    TextField(
                      controller: reasonCtrl,
                      maxLines: 2,
                      style: const TextStyle(color: Colors.white, fontSize: 13),
                      decoration: InputDecoration(
                        labelText: "السبب / التفاصيل (اختياري)",
                        labelStyle: const TextStyle(color: Colors.white70),
                        filled: true,
                        fillColor: const Color(0xFF1E1B4B),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),
                    if (errorMsg != null) ...[
                      const SizedBox(height: 10),
                      Container(
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          color: Colors.red.withOpacity(0.18),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: Colors.redAccent),
                        ),
                        child: Text(errorMsg!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.redAccent, fontSize: 12, fontWeight: FontWeight.bold)),
                      ),
                    ],
                    const SizedBox(height: 14),
                    ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF4F46E5),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                      ),
                      icon: submitting
                          ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                          : const Icon(Icons.send),
                      label: Text(submitting ? "جارٍ الإرسال..." : "إرسال الطلب للإدارة", style: const TextStyle(fontWeight: FontWeight.bold)),
                      onPressed: submitting
                          ? null
                          : () async {
                              setSheetState(() {
                                submitting = true;
                                errorMsg = null;
                              });
                              try {
                                final fp = await SecurityChecker.getDeviceFingerprint();
                                final tok = await SecurityChecker.getDeviceToken(employeeCode) ??
                                    await SecurityChecker.getAnySavedDeviceToken();
                                final res = await http
                                    .post(
                                      Uri.parse('$serverUrl/api/v1/requests/submit'),
                                      headers: {'Content-Type': 'application/json'},
                                      body: jsonEncode({
                                        'device_fingerprint': fp,
                                        'device_token': tok,
                                        'request_category': category,
                                        'request_subtype': subtype,
                                        'start_date': _fmtDateIso(startDate),
                                        'end_date': category == 'PERMISSION' ? _fmtDateIso(startDate) : _fmtDateIso(endDate),
                                        'from_time': category == 'PERMISSION' ? _fmtTimeHHMM(fromTime) : null,
                                        'to_time': category == 'PERMISSION' ? _fmtTimeHHMM(toTime) : null,
                                        'location_name': category == 'MISSION' ? locCtrl.text.trim() : null,
                                        'reason': reasonCtrl.text.trim().isNotEmpty ? reasonCtrl.text.trim() : null,
                                      }),
                                    )
                                    .timeout(const Duration(seconds: 15));
                                final data = jsonDecode(utf8.decode(res.bodyBytes));
                                if (res.statusCode == 201 && data['success'] == true) {
                                  if (ctx.mounted) Navigator.pop(ctx);
                                  if (mounted) {
                                    _showDialog("✅ تم إرسال الطلب", data['message'] ?? "تم إرسال طلبك بنجاح.");
                                    await _loadMyRequests();
                                  }
                                } else {
                                  setSheetState(() => errorMsg = data['detail'] ?? "تعذر إرسال الطلب");
                                }
                              } catch (e) {
                                setSheetState(() => errorMsg = "تعذر الاتصال بالسيرفر");
                              } finally {
                                if (ctx.mounted) setSheetState(() => submitting = false);
                              }
                            },
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bool showNavTabs = isVerified && isFaceEnrolled;
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
                onPressed: () => setState(() {
                  isVerified = false;
                  _selectedTabIndex = 0;
                }),
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
      body: isAutoChecking
          ? const Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  CircularProgressIndicator(color: Color(0xFF818CF8)),
                  SizedBox(height: 14),
                  Text(
                    "جارٍ التعرف على الهاتف المسجل...",
                    style: TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.bold),
                  ),
                ],
              ),
            )
          : (!isVerified
              ? Center(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.all(20),
                    child: _buildEmployeeLookupView(),
                  ),
                )
              : (_selectedTabIndex == 1 && isFaceEnrolled
                  ? _buildRequestsView()
                  : Center(
                      child: SingleChildScrollView(
                        padding: const EdgeInsets.all(20),
                        child: _buildPunchReadyView(),
                      ),
                    ))),
      bottomNavigationBar: showNavTabs
          ? NavigationBarTheme(
              data: NavigationBarThemeData(
                labelTextStyle: WidgetStateProperty.all(
                  const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
                ),
              ),
              child: NavigationBar(
                backgroundColor: const Color(0xFF1E1B4B),
                indicatorColor: const Color(0xFF4338CA),
                selectedIndex: _selectedTabIndex,
                onDestinationSelected: (idx) {
                  setState(() => _selectedTabIndex = idx);
                  if (idx == 1) _loadMyRequests();
                },
                destinations: const [
                  NavigationDestination(
                    icon: Icon(Icons.fingerprint, color: Colors.white70),
                    selectedIcon: Icon(Icons.fingerprint, color: Colors.white),
                    label: 'البصمة',
                  ),
                  NavigationDestination(
                    icon: Icon(Icons.event_note, color: Colors.white70),
                    selectedIcon: Icon(Icons.event_note, color: Colors.white),
                    label: 'الإجازات والأذونات',
                  ),
                ],
              ),
            )
          : null,
    );
  }

  Widget _buildBalanceCard(String title, double val, Color color) {
    final textVal = val == val.roundToDouble() ? val.toInt().toString() : val.toStringAsFixed(1);
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 8),
        decoration: BoxDecoration(
          color: color.withOpacity(0.15),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: color.withOpacity(0.5)),
        ),
        child: Column(
          children: [
            Text(title, style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(textVal, style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold)),
            const Text("يوم متبقي", style: TextStyle(color: Colors.white54, fontSize: 10)),
          ],
        ),
      ),
    );
  }

  Widget _buildRequestsView() {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: RefreshIndicator(
        onRefresh: _loadMyRequests,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Row(
              children: [
                _buildBalanceCard("اعتيادي", balAnnual, Colors.lightBlueAccent),
                const SizedBox(width: 8),
                _buildBalanceCard("عارضة", balCasual, Colors.amberAccent),
                const SizedBox(width: 8),
                _buildBalanceCard("مرضي", balSick, Colors.greenAccent),
              ],
            ),
            const SizedBox(height: 14),
            ElevatedButton.icon(
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF4F46E5),
                foregroundColor: Colors.white,
                padding: const EdgeInsets.symmetric(vertical: 14),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
              ),
              onPressed: _openNewRequestSheet,
              icon: const Icon(Icons.add_circle_outline),
              label: const Text("تقديم طلب جديد (إجازة / إذن / مأمورية)", style: TextStyle(fontWeight: FontWeight.bold)),
            ),
            const SizedBox(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text("📋 سجل طلباتي", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14)),
                TextButton.icon(
                  onPressed: isLoadingRequests ? null : _loadMyRequests,
                  icon: const Icon(Icons.refresh, size: 16, color: Color(0xFF818CF8)),
                  label: const Text("تحديث", style: TextStyle(color: Color(0xFF818CF8), fontSize: 12)),
                ),
              ],
            ),
            if (isLoadingRequests)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: CircularProgressIndicator(color: Color(0xFF818CF8))),
              )
            else if (myRequests.isEmpty)
              Container(
                padding: const EdgeInsets.all(24),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E1B4B).withOpacity(0.45),
                  borderRadius: BorderRadius.circular(18),
                ),
                child: const Text(
                  "لا توجد طلبات مسجلة حتى الآن.\nاضغط الزر أعلاه لتقديم إجازة أو إذن أو مأمورية.",
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.white60, fontSize: 12),
                ),
              )
            else
              ...myRequests.map((r) {
                final String st = (r['status'] ?? 'PENDING').toString();
                final Color stColor = st == 'APPROVED'
                    ? Colors.greenAccent
                    : (st == 'REJECTED' ? Colors.redAccent : (st == 'PENDING' ? Colors.amberAccent : Colors.white54));
                final String stLabel = st == 'APPROVED'
                    ? '✅ مقبول'
                    : (st == 'REJECTED' ? '❌ مرفوض' : (st == 'PENDING' ? '⏳ قيد المراجعة' : 'ملغي'));
                final String period = r['request_category'] == 'PERMISSION'
                    ? "${r['start_date']} • ${r['from_time'] ?? ''} - ${r['to_time'] ?? ''} (${r['duration_minutes'] ?? 0} د)"
                    : "${r['start_date']}${r['end_date'] != null && r['end_date'] != r['start_date'] ? ' ← ${r['end_date']}' : ''} (${r['days_count'] ?? 1} يوم)";

                return Container(
                  margin: const EdgeInsets.only(bottom: 10),
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1E1B4B).withOpacity(0.55),
                    borderRadius: BorderRadius.circular(18),
                    border: Border.all(color: const Color(0xFF3730A3)),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Expanded(
                            child: Text(
                              (r['subtype_label'] ?? '').toString(),
                              style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14),
                            ),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                            decoration: BoxDecoration(
                              color: stColor.withOpacity(0.15),
                              borderRadius: BorderRadius.circular(20),
                              border: Border.all(color: stColor.withOpacity(0.6)),
                            ),
                            child: Text(stLabel, style: TextStyle(color: stColor, fontSize: 11, fontWeight: FontWeight.bold)),
                          ),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Text(period, style: const TextStyle(color: Color(0xFFA5B4FC), fontSize: 12)),
                      if (r['location_name'] != null && r['location_name'].toString().isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text("📍 ${r['location_name']}", style: const TextStyle(color: Colors.tealAccent, fontSize: 12)),
                      ],
                      if (r['reason'] != null && r['reason'].toString().isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text(r['reason'].toString(), style: const TextStyle(color: Colors.white70, fontSize: 12)),
                      ],
                      if (r['admin_notes'] != null && r['admin_notes'].toString().isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Text("💬 الإدارة: ${r['admin_notes']}", style: const TextStyle(color: Colors.amberAccent, fontSize: 12)),
                      ],
                      if (st == 'PENDING') ...[
                        const SizedBox(height: 8),
                        Align(
                          alignment: Alignment.centerLeft,
                          child: TextButton(
                            onPressed: () => _cancelRequest(r['id'].toString()),
                            style: TextButton.styleFrom(foregroundColor: Colors.redAccent),
                            child: const Text("✖️ إلغاء الطلب", style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                          ),
                        ),
                      ],
                    ],
                  ),
                );
              }),
          ],
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
          margin: const EdgeInsets.only(bottom: 12),
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
        if (activeMissionLabel != null && activeMissionLabel!.isNotEmpty)
          Container(
            constraints: const BoxConstraints(maxWidth: 400),
            margin: const EdgeInsets.only(bottom: 14),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: Colors.teal.withOpacity(0.2),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Colors.tealAccent.withOpacity(0.6)),
            ),
            child: Text(
              "🚗 لديك مأمورية معتمدة اليوم: $activeMissionLabel",
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.tealAccent, fontSize: 12, fontWeight: FontWeight.bold),
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
