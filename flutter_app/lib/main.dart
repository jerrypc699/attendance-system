import 'dart:convert';
import 'dart:math' as math;
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

class _AttendanceHomeScreenState extends State<AttendanceHomeScreen>
    with SingleTickerProviderStateMixin {
  late final AnimationController _fxController;
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

  // HR Requests (Leaves, Permissions & Missions) + Direct Manager Approvals State
  int _selectedTabIndex = 0;
  int _requestsSubTab = 0; // 0 = طلباتي, 1 = طلبات فريقي
  String? activeMissionLabel;
  String? directManagerName;
  bool isManager = false;
  int teamPendingCount = 0;
  bool isLoadingRequests = false;
  bool isLoadingTeam = false;
  double balAnnual = 21.0;
  double balCasual = 6.0;
  double balSick = 15.0;
  double balPermRemHours = 4.0;
  double balPermQuotaHours = 4.0;
  int balPermUsedMinutes = 0;
  List<dynamic> myRequests = [];
  List<dynamic> teamRequests = [];

  CameraController? _controller;

  @override
  void initState() {
    super.initState();
    _fxController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 8),
    )..repeat();
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
            directManagerName = data['manager_name'];
            isManager = data['is_manager'] == true;
            teamPendingCount = (data['team_pending_count'] as num?)?.toInt() ?? 0;
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
    _fxController.dispose();
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
          directManagerName = data['manager_name'];
          isManager = data['is_manager'] == true;
          teamPendingCount = (data['team_pending_count'] as num?)?.toInt() ?? 0;
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
          balPermQuotaHours = (bals['permission_quota_hours'] as num?)?.toDouble() ?? 4.0;
          balPermRemHours = (bals['permission_remaining_hours'] as num?)?.toDouble() ?? balPermQuotaHours;
          balPermUsedMinutes = (bals['permission_used_minutes'] as num?)?.toInt() ?? 0;
          directManagerName = data['manager_name'];
          isManager = data['is_manager'] == true;
          teamPendingCount = (data['team_pending_count'] as num?)?.toInt() ?? 0;
          myRequests = List<dynamic>.from(data['requests'] ?? []);
        });
        if (isManager && _requestsSubTab == 1) {
          _loadTeamRequests();
        }
      }
    } catch (_) {} finally {
      if (mounted) setState(() => isLoadingRequests = false);
    }
  }

  Future<void> _loadTeamRequests() async {
    if (!mounted) return;
    setState(() => isLoadingTeam = true);
    try {
      final fp = await SecurityChecker.getDeviceFingerprint();
      final tok = await SecurityChecker.getDeviceToken(employeeCode) ??
          await SecurityChecker.getAnySavedDeviceToken();
      if (tok == null || tok.isEmpty) {
        if (mounted) setState(() => isLoadingTeam = false);
        return;
      }
      final res = await http
          .post(
            Uri.parse('$serverUrl/api/v1/requests/team-pending'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'device_fingerprint': fp, 'device_token': tok}),
          )
          .timeout(const Duration(seconds: 15));

      if (res.statusCode == 200 && mounted) {
        final data = jsonDecode(utf8.decode(res.bodyBytes));
        setState(() {
          isManager = data['is_manager'] == true;
          teamPendingCount = (data['team_pending_count'] as num?)?.toInt() ?? 0;
          teamRequests = List<dynamic>.from(data['requests'] ?? []);
        });
      }
    } catch (_) {} finally {
      if (mounted) setState(() => isLoadingTeam = false);
    }
  }

  Future<void> _openManagerReviewDialog(Map<String, dynamic> req, String decision) async {
    final noteCtrl = TextEditingController();
    final isApprove = decision == 'APPROVED';
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => Directionality(
        textDirection: TextDirection.rtl,
        child: AlertDialog(
          title: Text(isApprove ? "✅ اعتماد طلب موظف" : "❌ رفض طلب موظف"),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                "${req['employee_name']} (${req['employee_code']})\n${req['subtype_label']} • ${req['start_date']}",
                style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: noteCtrl,
                maxLines: 2,
                decoration: const InputDecoration(
                  labelText: "ملاحظة للموظف (اختياري)",
                  border: OutlineInputBorder(),
                ),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("إلغاء")),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: isApprove ? const Color(0xFF059669) : const Color(0xFFDC2626),
                foregroundColor: Colors.white,
              ),
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(isApprove ? "تأكيد الموافقة" : "تأكيد الرفض"),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true) return;

    try {
      final fp = await SecurityChecker.getDeviceFingerprint();
      final tok = await SecurityChecker.getDeviceToken(employeeCode) ??
          await SecurityChecker.getAnySavedDeviceToken();
      final res = await http
          .post(
            Uri.parse('$serverUrl/api/v1/requests/team/${req['id']}/review'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'device_fingerprint': fp,
              'device_token': tok,
              'decision': decision,
              'manager_notes': noteCtrl.text.trim().isNotEmpty ? noteCtrl.text.trim() : null,
            }),
          )
          .timeout(const Duration(seconds: 15));
      final data = jsonDecode(utf8.decode(res.bodyBytes));
      if (res.statusCode == 200 && data['success'] == true) {
        if (mounted) {
          _showDialog(isApprove ? "✅ تم الاعتماد" : "❌ تم الرفض", data['message'] ?? "تم تحديث الطلب.");
        }
        await _loadTeamRequests();
      } else if (mounted) {
        _showDialog("تنبيه", data['detail'] ?? "تعذر تنفيذ القرار.");
      }
    } catch (_) {
      if (mounted) _showDialog("خطأ", "تعذر الاتصال بالسيرفر.");
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
      backgroundColor: const Color(0xFF030712),
      body: isAutoChecking
          ? Stack(
              children: [
                Positioned.fill(
                  child: AnimatedBuilder(
                    animation: _fxController,
                    builder: (_, __) => CustomPaint(
                      painter: _CyberNeuralPainter(_fxController.value),
                    ),
                  ),
                ),
                Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      SizedBox(
                        width: 112,
                        height: 112,
                        child: AnimatedBuilder(
                          animation: _fxController,
                          builder: (_, __) => CustomPaint(
                            painter: _BiometricRadarPainter(_fxController.value),
                            child: const Center(
                              child: Icon(Icons.fingerprint, size: 42, color: Color(0xFF22D3EE)),
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(height: 18),
                      const Text(
                        "جارٍ التعرف المشفّر على الهاتف...",
                        style: TextStyle(color: Color(0xFF67E8F9), fontSize: 14, fontWeight: FontWeight.bold),
                      ),
                      const SizedBox(height: 6),
                      const Text(
                        "AI Biometric Security Engine",
                        style: TextStyle(color: Colors.white54, fontSize: 11, letterSpacing: 1.2),
                      ),
                    ],
                  ),
                ),
              ],
            )
          : (!isVerified
              ? Stack(
                  children: [
                    Positioned.fill(
                      child: AnimatedBuilder(
                        animation: _fxController,
                        builder: (_, __) => CustomPaint(
                          painter: _CyberNeuralPainter(_fxController.value),
                        ),
                      ),
                    ),
                    Center(
                      child: SingleChildScrollView(
                        padding: const EdgeInsets.all(20),
                        child: _buildEmployeeLookupView(),
                      ),
                    ),
                  ],
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
                destinations: [
                  const NavigationDestination(
                    icon: Icon(Icons.fingerprint, color: Colors.white70),
                    selectedIcon: Icon(Icons.fingerprint, color: Colors.white),
                    label: 'البصمة',
                  ),
                  NavigationDestination(
                    icon: Badge(
                      isLabelVisible: teamPendingCount > 0,
                      label: Text('$teamPendingCount'),
                      child: const Icon(Icons.event_note, color: Colors.white70),
                    ),
                    selectedIcon: Badge(
                      isLabelVisible: teamPendingCount > 0,
                      label: Text('$teamPendingCount'),
                      child: const Icon(Icons.event_note, color: Colors.white),
                    ),
                    label: 'الإجازات والأذونات',
                  ),
                ],
              ),
            )
          : null,
    );
  }

  Widget _buildBalanceCard(String title, String valueText, String subtitle, Color color) {
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
            Text(valueText, style: const TextStyle(color: Colors.white, fontSize: 19, fontWeight: FontWeight.bold)),
            Text(subtitle, style: const TextStyle(color: Colors.white54, fontSize: 10), textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }

  String _fmtNum(double v) => v == v.roundToDouble() ? v.toInt().toString() : v.toStringAsFixed(1);

  Widget _buildRequestsView() {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: RefreshIndicator(
        onRefresh: () async {
          await _loadMyRequests();
          if (isManager) await _loadTeamRequests();
        },
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Container(
              margin: const EdgeInsets.only(bottom: 12),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: const Color(0xFF1E1B4B).withOpacity(0.7),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: const Color(0xFF4338CA)),
              ),
              child: Text(
                directManagerName != null
                    ? "👔 جهة اعتماد طلباتك: المدير المباشر ($directManagerName)"
                    : "👔 جهة اعتماد طلباتك: الإدارة العليا",
                textAlign: TextAlign.center,
                style: const TextStyle(color: Color(0xFFC7D2FE), fontSize: 12, fontWeight: FontWeight.bold),
              ),
            ),
            if (isManager) ...[
              Container(
                margin: const EdgeInsets.only(bottom: 14),
                padding: const EdgeInsets.all(4),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E1B4B),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: const Color(0xFF3730A3)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: GestureDetector(
                        onTap: () => setState(() => _requestsSubTab = 0),
                        child: Container(
                          padding: const EdgeInsets.symmetric(vertical: 10),
                          decoration: BoxDecoration(
                            color: _requestsSubTab == 0 ? const Color(0xFF4F46E5) : Colors.transparent,
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: const Text(
                            "📋 طلباتي",
                            textAlign: TextAlign.center,
                            style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 12),
                          ),
                        ),
                      ),
                    ),
                    Expanded(
                      child: GestureDetector(
                        onTap: () {
                          setState(() => _requestsSubTab = 1);
                          _loadTeamRequests();
                        },
                        child: Container(
                          padding: const EdgeInsets.symmetric(vertical: 10),
                          decoration: BoxDecoration(
                            color: _requestsSubTab == 1 ? const Color(0xFF4F46E5) : Colors.transparent,
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: Text(
                            "👔 طلبات فريقي ($teamPendingCount)",
                            textAlign: TextAlign.center,
                            style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 12),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            if (_requestsSubTab == 1 && isManager) ..._buildManagerTeamSection() else ...[
            Row(
              children: [
                _buildBalanceCard("🏖️ اعتيادي", _fmtNum(balAnnual), "يوم متبقي", Colors.lightBlueAccent),
                const SizedBox(width: 8),
                _buildBalanceCard("⚡ عارضة", _fmtNum(balCasual), "يوم متبقي", Colors.amberAccent),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                _buildBalanceCard("🏥 مرضي", _fmtNum(balSick), "يوم متبقي", Colors.greenAccent),
                const SizedBox(width: 8),
                _buildBalanceCard(
                  "⏰ أذونات الشهر",
                  "${_fmtNum(balPermRemHours)} س",
                  "من ${_fmtNum(balPermQuotaHours)} س (مستخدم $balPermUsedMinutes د)",
                  Colors.purpleAccent,
                ),
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
                      if (r['reviewed_by'] != null && r['reviewed_by'].toString().isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text("✍️ ${r['reviewed_by']}", style: const TextStyle(color: Colors.greenAccent, fontSize: 11)),
                      ],
                      if ((r['manager_notes'] ?? r['admin_notes']) != null && (r['manager_notes'] ?? r['admin_notes']).toString().isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Text("💬 ملاحظة: ${r['manager_notes'] ?? r['admin_notes']}", style: const TextStyle(color: Colors.amberAccent, fontSize: 12)),
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
          ],
        ),
      ),
    );
  }

  List<Widget> _buildManagerTeamSection() {
    if (isLoadingTeam) {
      return const [
        Padding(
          padding: EdgeInsets.all(32),
          child: Center(child: CircularProgressIndicator(color: Color(0xFF818CF8))),
        ),
      ];
    }
    if (teamRequests.isEmpty) {
      return [
        Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: const Color(0xFF1E1B4B).withOpacity(0.45),
            borderRadius: BorderRadius.circular(18),
          ),
          child: const Text(
            "لا توجد طلبات من الموظفين التابعين لك حالياً.",
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white60, fontSize: 13),
          ),
        ),
      ];
    }
    return teamRequests.map((raw) {
      final r = Map<String, dynamic>.from(raw as Map);
      final String st = (r['status'] ?? 'PENDING').toString();
      final Color stColor = st == 'APPROVED'
          ? Colors.greenAccent
          : (st == 'REJECTED' ? Colors.redAccent : (st == 'PENDING' ? Colors.amberAccent : Colors.white54));
      final String stLabel = st == 'APPROVED'
          ? '✅ معتمد'
          : (st == 'REJECTED' ? '❌ مرفوض' : (st == 'PENDING' ? '⏳ بانتظار قرارك' : 'ملغي'));
      final String period = r['request_category'] == 'PERMISSION'
          ? "${r['start_date']} • ${r['from_time'] ?? ''} - ${r['to_time'] ?? ''} (${r['duration_minutes'] ?? 0} د)"
          : "${r['start_date']}${r['end_date'] != null && r['end_date'] != r['start_date'] ? ' ← ${r['end_date']}' : ''} (${r['days_count'] ?? 1} يوم)";

      return Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: const Color(0xFF1E1B4B).withOpacity(0.65),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: st == 'PENDING' ? Colors.amberAccent.withOpacity(0.7) : const Color(0xFF3730A3)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Text(
                    "👤 ${r['employee_name']} (${r['employee_code']})",
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
            const SizedBox(height: 4),
            Text(
              "الرصيد — اعتيادي: ${r['employee_annual_balance']} • عارضة: ${r['employee_casual_balance']} • أذونات: ${r['employee_permission_remaining_hours'] ?? r['employee_permission_quota_hours'] ?? 4} س",
              style: const TextStyle(color: Colors.white60, fontSize: 11),
            ),
            const SizedBox(height: 6),
            Text((r['subtype_label'] ?? '').toString(), style: const TextStyle(color: Color(0xFFC7D2FE), fontWeight: FontWeight.bold, fontSize: 13)),
            const SizedBox(height: 3),
            Text(period, style: const TextStyle(color: Color(0xFFA5B4FC), fontSize: 12)),
            if (r['location_name'] != null && r['location_name'].toString().isNotEmpty) ...[
              const SizedBox(height: 4),
              Text("📍 ${r['location_name']}", style: const TextStyle(color: Colors.tealAccent, fontSize: 12)),
            ],
            if (r['reason'] != null && r['reason'].toString().isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(r['reason'].toString(), style: const TextStyle(color: Colors.white70, fontSize: 12)),
            ],
            if (st == 'PENDING') ...[
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF059669),
                        foregroundColor: Colors.white,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                      onPressed: () => _openManagerReviewDialog(r, 'APPROVED'),
                      icon: const Icon(Icons.check, size: 16),
                      label: const Text("موافقة", style: TextStyle(fontWeight: FontWeight.bold)),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFFDC2626),
                        foregroundColor: Colors.white,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                      onPressed: () => _openManagerReviewDialog(r, 'REJECTED'),
                      icon: const Icon(Icons.close, size: 16),
                      label: const Text("رفض", style: TextStyle(fontWeight: FontWeight.bold)),
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      );
    }).toList();
  }

  Widget _buildEmployeeLookupView() {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 410),
        padding: const EdgeInsets.all(1.8),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(28),
          gradient: const LinearGradient(
            begin: Alignment.topRight,
            end: Alignment.bottomLeft,
            colors: [
              Color(0xCC22D3EE),
              Color(0xAA6366F1),
              Color(0xCCA855F7),
              Color(0x9910B981),
            ],
          ),
          boxShadow: [
            BoxShadow(
              color: const Color(0xFF4F46E5).withOpacity(0.35),
              blurRadius: 36,
              spreadRadius: 2,
            ),
          ],
        ),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 26),
          decoration: BoxDecoration(
            color: const Color(0xEB070D1F),
            borderRadius: BorderRadius.circular(26.5),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Center(
                child: SizedBox(
                  width: 116,
                  height: 116,
                  child: AnimatedBuilder(
                    animation: _fxController,
                    builder: (_, __) => CustomPaint(
                      painter: _BiometricRadarPainter(_fxController.value),
                      child: Center(
                        child: Container(
                          width: 58,
                          height: 58,
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(18),
                            color: const Color(0xFF0F172A),
                            border: Border.all(color: const Color(0xFF22D3EE).withOpacity(0.65), width: 1.5),
                            boxShadow: [
                              BoxShadow(
                                color: const Color(0xFF22D3EE).withOpacity(0.35),
                                blurRadius: 18,
                              ),
                            ],
                          ),
                          child: const Icon(Icons.face_retouching_natural, size: 32, color: Color(0xFF67E8F9)),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Center(
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1E1B4B).withOpacity(0.9),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: const Color(0xFF22D3EE).withOpacity(0.45)),
                  ),
                  child: const Text(
                    "● AI BIOMETRIC ACCESS",
                    style: TextStyle(color: Color(0xFF67E8F9), fontSize: 10, fontWeight: FontWeight.bold, letterSpacing: 1.1),
                  ),
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                "بوابة الحضور الذكي للموظفين",
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white, fontSize: 19, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 5),
              const Text(
                "أدخل كودك الوظيفي للتحقق الآمن ببصمة الوجه والموقع الحقيقي",
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white60, fontSize: 12),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  _buildMiniTechChip("🤖 بصمة AI", const Color(0xFF22D3EE)),
                  const SizedBox(width: 6),
                  _buildMiniTechChip("🛰️ GPS حقيقي", const Color(0xFF818CF8)),
                  const SizedBox(width: 6),
                  _buildMiniTechChip("🔒 ربط مشفّر", const Color(0xFF34D399)),
                ],
              ),
              const SizedBox(height: 18),
              TextField(
                controller: _codeController,
                textAlign: TextAlign.center,
                textCapitalization: TextCapitalization.characters,
                style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold, letterSpacing: 1.5),
                decoration: InputDecoration(
                  hintText: "مثال: EMP-2001",
                  hintStyle: const TextStyle(color: Colors.white38, fontSize: 14, letterSpacing: 0),
                  prefixIcon: const Icon(Icons.badge_outlined, color: Color(0xFF22D3EE)),
                  filled: true,
                  fillColor: const Color(0xFF0B1120),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(16),
                    borderSide: BorderSide(color: const Color(0xFF6366F1).withOpacity(0.55), width: 1.5),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(16),
                    borderSide: const BorderSide(color: Color(0xFF22D3EE), width: 2),
                  ),
                ),
                onSubmitted: _fetchEmployeeData,
              ),
              const SizedBox(height: 18),
              Container(
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(16),
                  gradient: const LinearGradient(
                    colors: [Color(0xFF4F46E5), Color(0xFF2563EB), Color(0xFF06B6D4)],
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: const Color(0xFF2563EB).withOpacity(0.45),
                      blurRadius: 18,
                      offset: const Offset(0, 6),
                    ),
                  ],
                ),
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.transparent,
                    shadowColor: Colors.transparent,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                  ),
                  onPressed: isProcessing ? null : () => _fetchEmployeeData(_codeController.text),
                  icon: isProcessing
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                      : const Icon(Icons.lock_open_rounded),
                  label: Text(
                    isProcessing ? "جارٍ التحقق..." : "متابعة للدخول الآمن",
                    style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildMiniTechChip(String text, Color color) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
        decoration: BoxDecoration(
          color: color.withOpacity(0.1),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: color.withOpacity(0.3)),
        ),
        child: Text(
          text,
          textAlign: TextAlign.center,
          style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.bold),
        ),
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

/// Animated 60fps Cyber Neural-Network & Ambient Aurora Backdrop for Login/Splash
class _CyberNeuralPainter extends CustomPainter {
  final double progress;
  _CyberNeuralPainter(this.progress);

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    // Deep Space Obsidian Gradient
    canvas.drawRect(
      rect,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topRight,
          end: Alignment.bottomLeft,
          colors: [Color(0xFF030712), Color(0xFF090F26), Color(0xFF131135)],
        ).createShader(rect),
    );

    // Subtle Drifting Tech Grid
    final gridPaint = Paint()
      ..color = const Color(0xFF22D3EE).withOpacity(0.055)
      ..strokeWidth = 0.8;
    const step = 38.0;
    final shift = (progress * step) % step;
    for (double x = shift; x < size.width; x += step) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), gridPaint);
    }
    for (double y = shift; y < size.height; y += step) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }

    // Pulsing Ambient Aurora Orbs
    final t = progress * 2 * math.pi;
    final orb1Center = Offset(size.width * 0.82 + math.sin(t) * 24, size.height * 0.16 + math.cos(t) * 18);
    canvas.drawCircle(
      orb1Center,
      150,
      Paint()
        ..shader = RadialGradient(
          colors: [const Color(0xFF4F46E5).withOpacity(0.28), Colors.transparent],
        ).createShader(Rect.fromCircle(center: orb1Center, radius: 150)),
    );
    final orb2Center = Offset(size.width * 0.18 + math.cos(t) * 22, size.height * 0.84 + math.sin(t) * 20);
    canvas.drawCircle(
      orb2Center,
      160,
      Paint()
        ..shader = RadialGradient(
          colors: [const Color(0xFF06B6D4).withOpacity(0.22), Colors.transparent],
        ).createShader(Rect.fromCircle(center: orb2Center, radius: 160)),
    );

    // Deterministic Floating Neural Nodes + Synaptic Lines
    const nodeCount = 22;
    final pts = <Offset>[];
    for (int i = 0; i < nodeCount; i++) {
      final seedA = (i * 1.6180339) % 1.0;
      final seedB = (i * 0.7548776) % 1.0;
      final px = ((seedA + math.sin(t + i * 0.9) * 0.07) % 1.0) * size.width;
      final py = ((seedB + math.cos(t + i * 1.1) * 0.07) % 1.0) * size.height;
      pts.add(Offset(px.clamp(0, size.width), py.clamp(0, size.height)));
    }

    final linePaint = Paint()..strokeWidth = 0.8;
    for (int i = 0; i < pts.length; i++) {
      for (int j = i + 1; j < pts.length; j++) {
        final dist = (pts[i] - pts[j]).distance;
        if (dist < 115) {
          linePaint.color = const Color(0xFF38BDF8).withOpacity((1 - dist / 115) * 0.3);
          canvas.drawLine(pts[i], pts[j], linePaint);
        }
      }
      final nodeColor = i % 3 == 0
          ? const Color(0xFF22D3EE)
          : (i % 3 == 1 ? const Color(0xFF818CF8) : const Color(0xFFA855F7));
      canvas.drawCircle(pts[i], 2.2, Paint()..color = nodeColor.withOpacity(0.85));
    }
  }

  @override
  bool shouldRepaint(covariant _CyberNeuralPainter oldDelegate) => oldDelegate.progress != progress;
}

/// Animated Biometric HUD Radar Rings + Vertical Laser Scan Beam
class _BiometricRadarPainter extends CustomPainter {
  final double progress;
  _BiometricRadarPainter(this.progress);

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.width / 2;
    final angle = progress * 2 * math.pi;

    // Sonar Expanding Ring
    final sonarPhase = (progress * 2) % 1.0;
    canvas.drawCircle(
      center,
      radius * (0.72 + sonarPhase * 0.42),
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.4
        ..color = const Color(0xFF22D3EE).withOpacity((1.0 - sonarPhase) * 0.55),
    );

    // Outer Rotating Segmented Cyan/Indigo HUD Arcs
    final outerRect = Rect.fromCircle(center: center, radius: radius - 3);
    final outerPaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.4
      ..strokeCap = StrokeCap.round;
    for (int k = 0; k < 4; k++) {
      outerPaint.color = k.isEven ? const Color(0xFF22D3EE) : const Color(0xFF818CF8);
      canvas.drawArc(outerRect, angle + k * (math.pi / 2), math.pi / 3.2, false, outerPaint);
    }

    // Inner Counter-Rotating Precision Arcs
    final innerRect = Rect.fromCircle(center: center, radius: radius - 11);
    final innerPaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4
      ..color = const Color(0xFFA855F7).withOpacity(0.65);
    for (int k = 0; k < 6; k++) {
      canvas.drawArc(innerRect, -angle * 1.5 + k * (math.pi / 3), math.pi / 7, false, innerPaint);
    }

    // Sweeping Vertical Laser Scan Beam inside Emblem Box
    final sweep = (math.sin(progress * 4 * math.pi) + 1) / 2; // 0..1 smooth ping-pong
    final y = center.dy - 24 + sweep * 48;
    final beamRect = Rect.fromLTWH(center.dx - 26, y - 1.5, 52, 3);
    canvas.drawRect(
      beamRect,
      Paint()
        ..shader = const LinearGradient(
          colors: [Colors.transparent, Color(0xFF22D3EE), Color(0xFFFFFFFF), Color(0xFF22D3EE), Colors.transparent],
        ).createShader(beamRect),
    );
  }

  @override
  bool shouldRepaint(covariant _BiometricRadarPainter oldDelegate) => oldDelegate.progress != progress;
}
