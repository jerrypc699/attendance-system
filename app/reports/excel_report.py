import io
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.models import AttendanceLog, HRRequest

ARABIC_WEEKDAYS = {
    0: "الإثنين",
    1: "الثلاثاء",
    2: "الأربعاء",
    3: "الخميس",
    4: "الجمعة",
    5: "السبت",
    6: "الأحد",
}

CATEGORY_LABELS_AR = {
    "LEAVE": "إجازة",
    "PERMISSION": "إذن",
    "MISSION": "مأمورية",
}

SUBTYPE_LABELS_AR = {
    "ANNUAL": "إجازة اعتيادية",
    "CASUAL": "إجازة عارضة",
    "SICK": "إجازة مرضية",
    "UNPAID": "إجازة بدون أجر",
    "OFFICIAL": "إجازة رسمية / خاصة",
    "LATE_ARRIVAL": "إذن تأخير صباحي",
    "EARLY_LEAVE": "إذن انصراف مبكر",
    "MID_DAY": "إذن خروج أثناء العمل",
    "EXTERNAL_MISSION": "مأمورية عمل خارجية",
    "CLIENT_VISIT": "زيارة عميل / موقع",
    "REMOTE_WORK": "عمل عن بُعد",
}

STATUS_LABELS_AR = {
    "APPROVED": "مقبول",
    "PENDING": "قيد المراجعة",
    "REJECTED": "مرفوض",
    "CANCELLED": "ملغي",
}


def _to_local_dt(ts: Optional[datetime], tz_offset_hours: int) -> Optional[datetime]:
    if not ts:
        return None
    utc_dt = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return utc_dt.astimezone(timezone(timedelta(hours=tz_offset_hours)))


def _fmt_time_ar(dt: Optional[datetime]) -> str:
    if not dt:
        return "--"
    hour12 = dt.hour % 12 or 12
    period = "ص" if dt.hour < 12 else "م"
    return f"{hour12:02d}:{dt.minute:02d} {period}"


def _fmt_hours_mm(total_minutes: int) -> str:
    if total_minutes <= 0:
        return "00:00"
    h, m = divmod(total_minutes, 60)
    return f"{h:02d}:{m:02d}"


def _parse_work_start(work_start_str: str) -> tuple[int, int]:
    try:
        parts = str(work_start_str).strip().split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 9, 0


def _iter_clamped_dates(
    start_str: str,
    end_str: Optional[str],
    bound_from: Optional[str],
    bound_to: Optional[str],
) -> List[date]:
    try:
        d1 = date.fromisoformat(start_str)
        d2 = date.fromisoformat(end_str) if end_str else d1
    except ValueError:
        return []
    if bound_from:
        try:
            d1 = max(d1, date.fromisoformat(bound_from))
        except ValueError:
            pass
    if bound_to:
        try:
            d2 = min(d2, date.fromisoformat(bound_to))
        except ValueError:
            pass
    if d2 < d1:
        return []
    days: List[date] = []
    cur = d1
    while cur <= d2 and len(days) < 120:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _compute_worked_minutes(day_logs: List[AttendanceLog], tz_offset: int) -> int:
    """Computes total worked minutes across paired CHECK_IN -> CHECK_OUT punches on the same day."""
    ordered = sorted(day_logs, key=lambda l: _to_local_dt(l.server_timestamp, tz_offset))
    total_sec = 0.0
    open_in: Optional[datetime] = None

    for l in ordered:
        dt = _to_local_dt(l.server_timestamp, tz_offset)
        if l.punch_type == "CHECK_IN":
            if open_in is None:
                open_in = dt
        elif l.punch_type == "CHECK_OUT":
            if open_in is not None and dt and dt >= open_in:
                total_sec += (dt - open_in).total_seconds()
                open_in = None

    return max(0, int(round(total_sec / 60.0)))


def build_attendance_excel_workbook(
    logs: List[AttendanceLog],
    company_name: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    tz_offset_hours: int = 2,
    work_start_time: str = "09:00",
    late_grace_minutes: int = 15,
    hr_requests: Optional[List[HRRequest]] = None,
) -> bytes:
    """
    Generates an executive 4-sheet Arabic RTL Excel (.xlsx) report:
      Sheet 1: الحضور اليومي (مجمّع) — 1 row per Employee per Day (Check-In + Check-Out + Approved Leaves/Permissions/Missions)
      Sheet 2: ملخص الموظفين (الرواتب) — Per-Employee Payroll Summary + Leave/Permission/Mission totals & remaining balances
      Sheet 3: السجل التفصيلي والتدقيق — Full Raw Audit Trail (Approved + Rejected)
      Sheet 4: الإجازات والأذونات والمأموريات — Complete HR Requests Log with statuses & reviewer notes
    """
    wb = Workbook()
    hr_requests = hr_requests or []

    # Palette & Reusable Styles
    NAVY_FILL = PatternFill("solid", fgColor="1E1B4B")
    INDIGO_FILL = PatternFill("solid", fgColor="4338CA")
    SUBHEADER_FILL = PatternFill("solid", fgColor="EEF2FF")
    ZEBRA_FILL = PatternFill("solid", fgColor="F8FAFC")
    TOTAL_FILL = PatternFill("solid", fgColor="E0E7FF")

    GREEN_FILL = PatternFill("solid", fgColor="DCFCE7")
    AMBER_FILL = PatternFill("solid", fgColor="FEF3C7")
    ROSE_FILL = PatternFill("solid", fgColor="FEE2E2")
    SKY_FILL = PatternFill("solid", fgColor="E0F2FE")
    SLATE_FILL = PatternFill("solid", fgColor="E2E8F0")
    KPI_FILL = PatternFill("solid", fgColor="F1F5F9")

    FONT_FAMILY = "Arial"
    TITLE_FONT = Font(name=FONT_FAMILY, size=15, bold=True, color="FFFFFF")
    SUB_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="312E81")
    HEADER_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="FFFFFF")
    DATA_FONT = Font(name=FONT_FAMILY, size=10, color="0F172A")
    BOLD_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="0F172A")
    TOTAL_FONT = Font(name=FONT_FAMILY, size=11, bold=True, color="1E1B4B")

    GREEN_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="166534")
    AMBER_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="92400E")
    ROSE_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="991B1B")
    SKY_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="0369A1")
    SLATE_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="475569")

    THIN_SIDE = Side(style="thin", color="CBD5E1")
    MED_NAVY = Side(style="medium", color="1E1B4B")
    CELL_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
    TOTAL_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=MED_NAVY, bottom=MED_NAVY)

    CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
    RIGHT = Alignment(horizontal="right", vertical="center", wrap_text=True)

    ws_start_h, ws_start_m = _parse_work_start(work_start_time)
    now_local = _to_local_dt(datetime.now(timezone.utc), tz_offset_hours)
    period_str = f"من {date_from or 'البداية'} إلى {date_to or 'اليوم'}"
    tz_label = f"UTC+{tz_offset_hours}" if tz_offset_hours >= 0 else f"UTC{tz_offset_hours}"

    # ------------------------------------------------------------------
    # Index APPROVED HR Requests (Leaves, Permissions, Missions)
    # ------------------------------------------------------------------
    approved_perms_by_key: dict[tuple[str, str], List[HRRequest]] = defaultdict(list)
    approved_missions_by_key: dict[tuple[str, str], List[HRRequest]] = defaultdict(list)
    approved_leaves_by_key: dict[tuple[str, str], HRRequest] = {}

    emp_summary = defaultdict(
        lambda: {
            "code": "",
            "name": "",
            "dept": "",
            "days": 0,
            "complete_days": 0,
            "missing_out_days": 0,
            "total_minutes": 0,
            "total_late_minutes": 0,
            "late_days": 0,
            "rejected_count": 0,
            "leave_days": 0,
            "permission_minutes": 0,
            "mission_days": 0,
            "annual_balance": 21.0,
            "casual_balance": 6.0,
            "permission_quota_hours": 4.0,
        }
    )

    def _register_emp_meta(emp_id: str, emp_obj):
        if not emp_obj:
            return
        s = emp_summary[emp_id]
        s["code"] = emp_obj.employee_code or s["code"]
        s["name"] = emp_obj.full_name or s["name"]
        s["dept"] = (emp_obj.department or "العام")
        if emp_obj.annual_leave_balance is not None:
            s["annual_balance"] = round(float(emp_obj.annual_leave_balance), 1)
        if emp_obj.casual_leave_balance is not None:
            s["casual_balance"] = round(float(emp_obj.casual_leave_balance), 1)
        if getattr(emp_obj, "monthly_permission_hours", None) is not None:
            s["permission_quota_hours"] = round(float(emp_obj.monthly_permission_hours), 1)

    emp_leave_dates: dict[str, set] = defaultdict(set)
    emp_mission_dates: dict[str, set] = defaultdict(set)

    for req in hr_requests:
        if req.employee:
            _register_emp_meta(req.employee_id, req.employee)
        if req.status != "APPROVED":
            continue
        if req.request_category == "PERMISSION":
            approved_perms_by_key[(req.employee_id, req.start_date)].append(req)
            emp_summary[req.employee_id]["permission_minutes"] += int(req.duration_minutes or 0)
        elif req.request_category == "MISSION":
            for d_obj in _iter_clamped_dates(req.start_date, req.end_date, date_from, date_to):
                ds = d_obj.isoformat()
                approved_missions_by_key[(req.employee_id, ds)].append(req)
                emp_mission_dates[req.employee_id].add(ds)
        elif req.request_category == "LEAVE":
            for d_obj in _iter_clamped_dates(req.start_date, req.end_date, date_from, date_to):
                ds = d_obj.isoformat()
                approved_leaves_by_key[(req.employee_id, ds)] = req
                emp_leave_dates[req.employee_id].add(ds)

    for eid, dset in emp_leave_dates.items():
        emp_summary[eid]["leave_days"] = len(dset)
    for eid, dset in emp_mission_dates.items():
        emp_summary[eid]["mission_days"] = len(dset)

    # ------------------------------------------------------------------
    # Group APPROVED punches by (local_date_str, employee_id)
    # ------------------------------------------------------------------
    approved_logs = [l for l in logs if l.status == "APPROVED"]
    rejected_count_by_emp = defaultdict(int)
    for l in logs:
        if l.employee:
            _register_emp_meta(l.employee_id, l.employee)
        if l.status == "REJECTED":
            rejected_count_by_emp[l.employee_id] += 1

    daily_groups = defaultdict(list)
    for l in approved_logs:
        ldt = _to_local_dt(l.server_timestamp, tz_offset_hours)
        if not ldt:
            continue
        date_key = ldt.strftime("%Y-%m-%d")
        daily_groups[(date_key, l.employee_id)].append(l)

    daily_records = []

    for (date_str, emp_id), day_punches in daily_groups.items():
        day_punches.sort(key=lambda l: _to_local_dt(l.server_timestamp, tz_offset_hours))
        sample = day_punches[0]
        emp = sample.employee
        if emp:
            _register_emp_meta(emp_id, emp)

        branch_names = []
        for p in day_punches:
            bname = p.branch.name if p.branch else None
            if bname and bname not in branch_names:
                branch_names.append(bname)

        check_ins = [p for p in day_punches if p.punch_type == "CHECK_IN"]
        check_outs = [p for p in day_punches if p.punch_type == "CHECK_OUT"]

        first_in_dt = _to_local_dt(check_ins[0].server_timestamp, tz_offset_hours) if check_ins else None
        last_out_dt = _to_local_dt(check_outs[-1].server_timestamp, tz_offset_hours) if check_outs else None
        ref_dt = first_in_dt or last_out_dt
        weekday_ar = ARABIC_WEEKDAYS.get(ref_dt.weekday(), "") if ref_dt else ""

        worked_min = _compute_worked_minutes(day_punches, tz_offset_hours)
        worked_dec = round(worked_min / 60.0, 2)

        # Late minutes relative to work_start_time + late_grace_minutes, minus approved LATE_ARRIVAL permission
        late_min = 0
        if first_in_dt:
            expected_start = first_in_dt.replace(hour=ws_start_h, minute=ws_start_m, second=0, microsecond=0)
            grace_cutoff = expected_start + timedelta(minutes=late_grace_minutes)
            if first_in_dt > grace_cutoff:
                late_min = int((first_in_dt - expected_start).total_seconds() // 60)

        day_perms = approved_perms_by_key.get((emp_id, date_str), [])
        day_missions = approved_missions_by_key.get((emp_id, date_str), [])
        notes_parts: List[str] = []

        excused_late_mins = sum(
            int(pr.duration_minutes or 0) for pr in day_perms if pr.request_subtype == "LATE_ARRIVAL"
        )
        if excused_late_mins > 0:
            late_min = max(0, late_min - excused_late_mins)
            notes_parts.append(f"إذن تأخير معتمد ({excused_late_mins} د)")

        for pr in day_perms:
            if pr.request_subtype != "LATE_ARRIVAL":
                lbl = SUBTYPE_LABELS_AR.get(pr.request_subtype, pr.request_subtype)
                notes_parts.append(f"{lbl} ({pr.duration_minutes or 0} د)")

        if first_in_dt and last_out_dt:
            day_status = "حضور مكتمل"
            status_kind = "complete"
        elif first_in_dt and not last_out_dt:
            day_status = "حضور بدون انصراف"
            status_kind = "missing_out"
        else:
            day_status = "انصراف بدون حضور"
            status_kind = "missing_in"

        if day_missions:
            day_status = f"{day_status} (مأمورية)"
            m_locs = [m.location_name or SUBTYPE_LABELS_AR.get(m.request_subtype, "مأمورية") for m in day_missions]
            notes_parts.append("مأمورية: " + " / ".join(dict.fromkeys(m_locs)))

        sims = [p.face_similarity_score for p in day_punches if p.face_similarity_score is not None]
        avg_sim = round((sum(sims) / len(sims)) * 100.0, 1) if sims else None
        dists = [p.distance_to_branch_meters for p in day_punches if p.distance_to_branch_meters is not None]
        min_dist = round(min(dists), 1) if dists else None

        rec = {
            "date": date_str,
            "weekday": weekday_ar,
            "emp_code": emp.employee_code if emp else "",
            "emp_name": emp.full_name if emp else "غير معروف",
            "dept": (emp.department or "العام") if emp else "العام",
            "branch": " / ".join(branch_names) if branch_names else ("مأمورية خارجية" if day_missions else "--"),
            "in_time": _fmt_time_ar(first_in_dt),
            "out_time": _fmt_time_ar(last_out_dt),
            "worked_hhmm": _fmt_hours_mm(worked_min),
            "worked_dec": worked_dec,
            "late_min": late_min,
            "status": day_status,
            "status_kind": status_kind,
            "avg_sim": avg_sim,
            "min_dist": min_dist,
            "punch_count": len(day_punches),
            "notes": " | ".join(notes_parts) if notes_parts else "--",
        }
        daily_records.append(rec)

        s = emp_summary[emp_id]
        s["days"] += 1
        if status_kind == "complete":
            s["complete_days"] += 1
        elif status_kind == "missing_out":
            s["missing_out_days"] += 1
        s["total_minutes"] += worked_min
        s["total_late_minutes"] += late_min
        if late_min > 0:
            s["late_days"] += 1

    # Inject Sky-Blue Rows for Approved Leave Days (when employee had no punch that day)
    for (emp_id, date_str), lv in approved_leaves_by_key.items():
        if (date_str, emp_id) in daily_groups:
            continue
        emp = lv.employee
        try:
            d_obj = date.fromisoformat(date_str)
            weekday_ar = ARABIC_WEEKDAYS.get(d_obj.weekday(), "")
        except ValueError:
            weekday_ar = ""
        sub_ar = SUBTYPE_LABELS_AR.get(lv.request_subtype, "إجازة")
        daily_records.append(
            {
                "date": date_str,
                "weekday": weekday_ar,
                "emp_code": emp.employee_code if emp else "",
                "emp_name": emp.full_name if emp else "غير معروف",
                "dept": (emp.department or "العام") if emp else "العام",
                "branch": "--",
                "in_time": "--",
                "out_time": "--",
                "worked_hhmm": "00:00",
                "worked_dec": 0.0,
                "late_min": 0,
                "status": f"{sub_ar} معتمدة",
                "status_kind": "leave",
                "avg_sim": None,
                "min_dist": None,
                "punch_count": 0,
                "notes": lv.reason or "إجازة معتمدة",
            }
        )

    for eid, cnt in rejected_count_by_emp.items():
        emp_summary[eid]["rejected_count"] = cnt

    # Sort combined daily rows: newest date first, then employee code
    daily_records.sort(key=lambda r: (r["date"], r["emp_code"]), reverse=True)

    # ==================================================================
    # SHEET 1: الحضور اليومي (مجمّع)
    # ==================================================================
    ws1 = wb.active
    ws1.title = "الحضور اليومي (مجمّع)"
    ws1.views.sheetView[0].rightToLeft = True
    ws1.page_setup.orientation = ws1.ORIENTATION_LANDSCAPE
    ws1.page_setup.fitToWidth = 1
    ws1.page_setup.fitToHeight = 0
    ws1.sheet_properties.pageSetUpPr.fitToPage = True

    headers_s1 = [
        "التاريخ",
        "اليوم",
        "كود الموظف",
        "اسم الموظف",
        "القسم",
        "الفرع",
        "وقت الحضور",
        "وقت الانصراف",
        "ساعات العمل (س:د)",
        "ساعات العمل (عشري)",
        "التأخير (دقيقة)",
        "حالة اليوم",
        "تطابق الوجه AI",
        "المسافة للفرع (م)",
        "عدد البصمات",
        "إذن / مأمورية / ملاحظات",
    ]
    col_count_1 = len(headers_s1)
    last_col_1 = get_column_letter(col_count_1)

    # Title Banner
    ws1.merge_cells(f"A1:{last_col_1}1")
    ws1["A1"] = f"تقرير الحضور والانصراف والإجازات اليومي المجمّع — {company_name}"
    ws1["A1"].font = TITLE_FONT
    ws1["A1"].fill = NAVY_FILL
    ws1["A1"].alignment = CENTER
    ws1.row_dimensions[1].height = 34

    ws1.merge_cells(f"A2:{last_col_1}2")
    ws1["A2"] = (
        f"الفترة: {period_str}   |   بداية الدوام الرسمي: {work_start_time} (سماح {late_grace_minutes} د)   |   "
        f"تاريخ الاستخراج: {now_local.strftime('%Y-%m-%d %H:%M')} ({tz_label})"
    )
    ws1["A2"].font = SUB_FONT
    ws1["A2"].fill = SUBHEADER_FILL
    ws1["A2"].alignment = CENTER
    ws1.row_dimensions[2].height = 22

    # KPI Summary Strip (Row 4)
    attend_days_all = sum(1 for r in daily_records if r["status_kind"] != "leave")
    leave_days_all = sum(1 for r in daily_records if r["status_kind"] == "leave")
    complete_all = sum(1 for r in daily_records if r["status_kind"] == "complete")
    missing_out_all = sum(1 for r in daily_records if r["status_kind"] == "missing_out")
    late_all = sum(1 for r in daily_records if r["late_min"] > 0)
    total_hours_all = round(sum(r["worked_dec"] for r in daily_records), 2)

    kpi_cells = [
        ("A4:C4", f"أيام الحضور: {attend_days_all}  |  إجازات: {leave_days_all}"),
        ("D4:F4", f"إجمالي ساعات العمل: {total_hours_all} ساعة"),
        ("G4:I4", f"أيام مكتملة (حضور+انصراف): {complete_all}"),
        ("J4:L4", f"حضور بدون انصراف: {missing_out_all}"),
        ("M4:P4", f"حالات التأخير (بعد الأذونات): {late_all}"),
    ]
    ws1.row_dimensions[3].height = 8
    ws1.row_dimensions[4].height = 24
    ws1.row_dimensions[5].height = 8
    for rng, label in kpi_cells:
        ws1.merge_cells(rng)
        top_left = rng.split(":")[0]
        cell = ws1[top_left]
        cell.value = label
        cell.font = BOLD_FONT
        cell.fill = KPI_FILL
        cell.alignment = CENTER
        for row_cells in ws1[rng]:
            for c in row_cells:
                c.border = CELL_BORDER
                c.fill = KPI_FILL

    # Table Header (Row 6)
    header_row_1 = 6
    ws1.row_dimensions[header_row_1].height = 26
    for idx, title in enumerate(headers_s1, start=1):
        cell = ws1.cell(row=header_row_1, column=idx, value=title)
        cell.font = HEADER_FONT
        cell.fill = INDIGO_FILL
        cell.alignment = CENTER
        cell.border = CELL_BORDER

    ws1.freeze_panes = "A7"

    # Data Rows
    data_start_1 = 7
    for r_idx, rec in enumerate(daily_records, start=data_start_1):
        ws1.row_dimensions[r_idx].height = 22
        is_even = (r_idx % 2 == 0)
        is_leave = rec["status_kind"] == "leave"
        row_values = [
            rec["date"],
            rec["weekday"],
            rec["emp_code"],
            rec["emp_name"],
            rec["dept"],
            rec["branch"],
            rec["in_time"],
            rec["out_time"],
            rec["worked_hhmm"],
            rec["worked_dec"],
            rec["late_min"],
            rec["status"],
            f"{rec['avg_sim']}%" if rec["avg_sim"] is not None else "--",
            rec["min_dist"] if rec["min_dist"] is not None else "--",
            rec["punch_count"],
            rec["notes"],
        ]
        for c_idx, val in enumerate(row_values, start=1):
            cell = ws1.cell(row=r_idx, column=c_idx, value=val)
            cell.font = BOLD_FONT if c_idx in (3, 4) else DATA_FONT
            cell.alignment = RIGHT if c_idx in (4, 5, 6, 16) else CENTER
            cell.border = CELL_BORDER
            if is_leave:
                cell.fill = SKY_FILL
                if c_idx == 12:
                    cell.font = SKY_FONT
            elif is_even:
                cell.fill = ZEBRA_FILL

            if c_idx == 10:
                cell.number_format = "0.00"
            elif c_idx == 11 and isinstance(val, int) and val > 0:
                cell.fill = ROSE_FILL
                cell.font = ROSE_FONT
            elif c_idx == 12 and not is_leave:
                if rec["status_kind"] == "complete":
                    cell.fill = GREEN_FILL
                    cell.font = GREEN_FONT
                elif rec["status_kind"] == "missing_out":
                    cell.fill = AMBER_FILL
                    cell.font = AMBER_FONT
                else:
                    cell.fill = ROSE_FILL
                    cell.font = ROSE_FONT

    # Totals Row with live Excel Formulas
    data_end_1 = data_start_1 + len(daily_records) - 1
    if daily_records:
        tot_row = data_end_1 + 1
        ws1.row_dimensions[tot_row].height = 26
        ws1.merge_cells(f"A{tot_row}:I{tot_row}")
        ws1[f"A{tot_row}"] = f"الإجمالي العام ({len(daily_records)} سجل يومي)"
        ws1[f"A{tot_row}"].alignment = CENTER
        ws1[f"J{tot_row}"] = f"=SUM(J{data_start_1}:J{data_end_1})"
        ws1[f"J{tot_row}"].number_format = "0.00"
        ws1[f"K{tot_row}"] = f"=SUM(K{data_start_1}:K{data_end_1})"
        ws1[f"O{tot_row}"] = f"=SUM(O{data_start_1}:O{data_end_1})"
        for c_idx in range(1, col_count_1 + 1):
            c = ws1.cell(row=tot_row, column=c_idx)
            c.font = TOTAL_FONT
            c.fill = TOTAL_FILL
            c.border = TOTAL_BORDER
            if c_idx >= 10:
                c.alignment = CENTER
        ws1.auto_filter.ref = f"A{header_row_1}:{last_col_1}{data_end_1}"
    else:
        ws1.merge_cells(f"A7:{last_col_1}7")
        ws1["A7"] = "لا توجد سجلات حضور أو إجازات معتمدة في الفترة المحددة"
        ws1["A7"].alignment = CENTER
        ws1["A7"].font = SUB_FONT

    widths_s1 = [13, 11, 13, 24, 15, 16, 13, 13, 15, 15, 14, 24, 14, 15, 12, 28]
    for i, w in enumerate(widths_s1, start=1):
        ws1.column_dimensions[get_column_letter(i)].width = w

    # ==================================================================
    # SHEET 2: ملخص الموظفين (الرواتب)
    # ==================================================================
    ws2 = wb.create_sheet("ملخص الموظفين (الرواتب)")
    ws2.views.sheetView[0].rightToLeft = True
    ws2.page_setup.orientation = ws2.ORIENTATION_LANDSCAPE
    ws2.page_setup.fitToWidth = 1
    ws2.page_setup.fitToHeight = 0
    ws2.sheet_properties.pageSetUpPr.fitToPage = True

    headers_s2 = [
        "كود الموظف",
        "اسم الموظف",
        "القسم",
        "أيام الحضور",
        "أيام مكتملة",
        "أيام بدون انصراف",
        "إجمالي الساعات (س:د)",
        "إجمالي الساعات (عشري)",
        "متوسط ساعات اليوم",
        "أيام التأخير",
        "إجمالي التأخير (دقيقة)",
        "محاولات مرفوضة",
        "أيام إجازات معتمدة",
        "دقائق أذونات معتمدة",
        "أيام مأموريات معتمدة",
        "رصيد اعتيادي متبقي",
        "رصيد عارضة متبقي",
        "رصيد أذونات شهري متبقي (س)",
    ]
    last_col_2 = get_column_letter(len(headers_s2))

    ws2.merge_cells(f"A1:{last_col_2}1")
    ws2["A1"] = f"ملخص الحضور والإجازات وساعات العمل والرواتب لكل موظف — {company_name}"
    ws2["A1"].font = TITLE_FONT
    ws2["A1"].fill = NAVY_FILL
    ws2["A1"].alignment = CENTER
    ws2.row_dimensions[1].height = 32

    ws2.merge_cells(f"A2:{last_col_2}2")
    ws2["A2"] = f"الفترة: {period_str}   |   تاريخ الاستخراج: {now_local.strftime('%Y-%m-%d %H:%M')} ({tz_label})"
    ws2["A2"].font = SUB_FONT
    ws2["A2"].fill = SUBHEADER_FILL
    ws2["A2"].alignment = CENTER
    ws2.row_dimensions[2].height = 22

    ws2.row_dimensions[4].height = 26
    for idx, title in enumerate(headers_s2, start=1):
        c = ws2.cell(row=4, column=idx, value=title)
        c.font = HEADER_FONT
        c.fill = INDIGO_FILL
        c.alignment = CENTER
        c.border = CELL_BORDER
    ws2.freeze_panes = "A5"

    sorted_emps = sorted(emp_summary.values(), key=lambda x: x["code"])
    for r_idx, es in enumerate(sorted_emps, start=5):
        ws2.row_dimensions[r_idx].height = 22
        total_dec = round(es["total_minutes"] / 60.0, 2)
        avg_dec = round(total_dec / es["days"], 2) if es["days"] else 0.0
        vals = [
            es["code"],
            es["name"],
            es["dept"],
            es["days"],
            es["complete_days"],
            es["missing_out_days"],
            _fmt_hours_mm(es["total_minutes"]),
            total_dec,
            avg_dec,
            es["late_days"],
            es["total_late_minutes"],
            es["rejected_count"],
            es["leave_days"],
            es["permission_minutes"],
            es["mission_days"],
            es["annual_balance"],
            es["casual_balance"],
            round(max(0.0, es["permission_quota_hours"] - (es["permission_minutes"] / 60.0)), 2),
        ]
        for c_idx, val in enumerate(vals, start=1):
            cell = ws2.cell(row=r_idx, column=c_idx, value=val)
            cell.font = BOLD_FONT if c_idx in (1, 2) else DATA_FONT
            cell.alignment = RIGHT if c_idx in (2, 3) else CENTER
            cell.border = CELL_BORDER
            if r_idx % 2 == 0:
                cell.fill = ZEBRA_FILL
            if c_idx in (8, 9):
                cell.number_format = "0.00"
            elif c_idx in (16, 17, 18):
                cell.number_format = "0.0"
                cell.fill = SKY_FILL
                cell.font = SKY_FONT
            if c_idx == 11 and val > 0:
                cell.fill = ROSE_FILL
                cell.font = ROSE_FONT

    if sorted_emps:
        end_s2 = 5 + len(sorted_emps) - 1
        tot2 = end_s2 + 1
        ws2.row_dimensions[tot2].height = 26
        ws2.merge_cells(f"A{tot2}:C{tot2}")
        ws2[f"A{tot2}"] = f"الإجمالي ({len(sorted_emps)} موظف)"
        ws2[f"A{tot2}"].alignment = CENTER
        for col_letter in ("D", "E", "F", "H", "J", "K", "L", "M", "N", "O"):
            ws2[f"{col_letter}{tot2}"] = f"=SUM({col_letter}5:{col_letter}{end_s2})"
            if col_letter == "H":
                ws2[f"{col_letter}{tot2}"].number_format = "0.00"
        for c_idx in range(1, len(headers_s2) + 1):
            c = ws2.cell(row=tot2, column=c_idx)
            c.font = TOTAL_FONT
            c.fill = TOTAL_FILL
            c.border = TOTAL_BORDER
            if c_idx >= 4:
                c.alignment = CENTER
        ws2.auto_filter.ref = f"A4:{last_col_2}{end_s2}"

    widths_s2 = [13, 24, 15, 12, 12, 15, 16, 16, 15, 12, 16, 13, 15, 16, 16, 15, 15, 19]
    for i, w in enumerate(widths_s2, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # ==================================================================
    # SHEET 3: السجل التفصيلي والتدقيق (All Raw Punches)
    # ==================================================================
    ws3 = wb.create_sheet("السجل التفصيلي والتدقيق")
    ws3.views.sheetView[0].rightToLeft = True
    ws3.page_setup.orientation = ws3.ORIENTATION_LANDSCAPE
    ws3.page_setup.fitToWidth = 1
    ws3.page_setup.fitToHeight = 0
    ws3.sheet_properties.pageSetUpPr.fitToPage = True

    headers_s3 = [
        "التاريخ",
        "الوقت المحلي",
        "كود الموظف",
        "اسم الموظف",
        "الفرع",
        "النوع",
        "الحالة",
        "المسافة (م)",
        "تطابق الوجه AI",
        "الحيوية",
        "IP الشركة",
        "QR",
        "Wi-Fi",
        "IP الموظف",
        "سبب الرفض",
    ]
    last_col_3 = get_column_letter(len(headers_s3))

    ws3.merge_cells(f"A1:{last_col_3}1")
    ws3["A1"] = f"السجل التفصيلي الكامل للبصمات والتدقيق الأمني — {company_name}"
    ws3["A1"].font = TITLE_FONT
    ws3["A1"].fill = NAVY_FILL
    ws3["A1"].alignment = CENTER
    ws3.row_dimensions[1].height = 30

    ws3.row_dimensions[3].height = 25
    for idx, title in enumerate(headers_s3, start=1):
        c = ws3.cell(row=3, column=idx, value=title)
        c.font = HEADER_FONT
        c.fill = INDIGO_FILL
        c.alignment = CENTER
        c.border = CELL_BORDER
    ws3.freeze_panes = "A4"

    for r_idx, l in enumerate(logs, start=4):
        ws3.row_dimensions[r_idx].height = 21
        ldt = _to_local_dt(l.server_timestamp, tz_offset_hours)
        vals = [
            ldt.strftime("%Y-%m-%d") if ldt else "--",
            _fmt_time_ar(ldt),
            l.employee.employee_code if l.employee else "",
            l.employee.full_name if l.employee else "غير معروف",
            l.branch.name if l.branch else "--",
            "حضور" if l.punch_type == "CHECK_IN" else "انصراف",
            "مقبول" if l.status == "APPROVED" else "مرفوض",
            round(l.distance_to_branch_meters, 1) if l.distance_to_branch_meters is not None else "--",
            f"{round(l.face_similarity_score * 100, 1)}%" if l.face_similarity_score is not None else "--",
            f"{round(l.liveness_score * 100, 1)}%" if l.liveness_score is not None else "--",
            "نعم" if l.office_ip_verified else "--",
            "نعم" if l.qr_verified else "--",
            "نعم" if l.wifi_bssid_verified else "--",
            l.client_ip or "--",
            l.rejection_reason or "--",
        ]
        for c_idx, val in enumerate(vals, start=1):
            cell = ws3.cell(row=r_idx, column=c_idx, value=val)
            cell.font = DATA_FONT
            cell.alignment = RIGHT if c_idx in (4, 5, 15) else CENTER
            cell.border = CELL_BORDER
            if l.status == "REJECTED":
                cell.fill = ROSE_FILL if c_idx == 7 else ZEBRA_FILL
                if c_idx in (7, 15):
                    cell.font = ROSE_FONT
            elif c_idx == 7:
                cell.fill = GREEN_FILL
                cell.font = GREEN_FONT

    if logs:
        ws3.auto_filter.ref = f"A3:{last_col_3}{3 + len(logs)}"

    widths_s3 = [13, 12, 13, 22, 15, 10, 11, 12, 14, 10, 10, 8, 9, 16, 36]
    for i, w in enumerate(widths_s3, start=1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    # ==================================================================
    # SHEET 4: الإجازات والأذونات والمأموريات (HR Requests Log)
    # ==================================================================
    ws4 = wb.create_sheet("الإجازات والأذونات والمأموريات")
    ws4.views.sheetView[0].rightToLeft = True
    ws4.page_setup.orientation = ws4.ORIENTATION_LANDSCAPE
    ws4.page_setup.fitToWidth = 1
    ws4.page_setup.fitToHeight = 0
    ws4.sheet_properties.pageSetUpPr.fitToPage = True

    headers_s4 = [
        "كود الموظف",
        "اسم الموظف",
        "القسم",
        "التصنيف",
        "نوع الطلب",
        "من تاريخ",
        "إلى تاريخ",
        "المدة",
        "من الساعة",
        "إلى الساعة",
        "المكان / الجهة",
        "السبب",
        "الحالة",
        "المدير المباشر",
        "جهة الاعتماد الفعلية",
        "ملاحظات المدير / الإدارة",
    ]
    last_col_4 = get_column_letter(len(headers_s4))

    ws4.merge_cells(f"A1:{last_col_4}1")
    ws4["A1"] = f"سجل طلبات الإجازات والأذونات والمأموريات — {company_name}"
    ws4["A1"].font = TITLE_FONT
    ws4["A1"].fill = NAVY_FILL
    ws4["A1"].alignment = CENTER
    ws4.row_dimensions[1].height = 32

    ws4.merge_cells(f"A2:{last_col_4}2")
    ws4["A2"] = f"الفترة: {period_str}   |   إجمالي الطلبات: {len(hr_requests)}   |   تاريخ الاستخراج: {now_local.strftime('%Y-%m-%d %H:%M')} ({tz_label})"
    ws4["A2"].font = SUB_FONT
    ws4["A2"].fill = SUBHEADER_FILL
    ws4["A2"].alignment = CENTER
    ws4.row_dimensions[2].height = 22

    ws4.row_dimensions[4].height = 26
    for idx, title in enumerate(headers_s4, start=1):
        c = ws4.cell(row=4, column=idx, value=title)
        c.font = HEADER_FONT
        c.fill = INDIGO_FILL
        c.alignment = CENTER
        c.border = CELL_BORDER
    ws4.freeze_panes = "A5"

    for r_idx, req in enumerate(hr_requests, start=5):
        ws4.row_dimensions[r_idx].height = 22
        if req.request_category == "PERMISSION":
            dur_str = f"{req.duration_minutes or 0} دقيقة"
        else:
            d_val = req.days_count or 1.0
            dur_str = f"{int(d_val) if float(d_val).is_integer() else d_val} يوم"

        mgr_obj = req.assigned_manager or (req.employee.manager if req.employee else None)
        vals = [
            req.employee.employee_code if req.employee else "",
            req.employee.full_name if req.employee else "غير معروف",
            (req.employee.department or "العام") if req.employee else "العام",
            CATEGORY_LABELS_AR.get(req.request_category, req.request_category),
            SUBTYPE_LABELS_AR.get(req.request_subtype, req.request_subtype),
            req.start_date,
            req.end_date,
            dur_str,
            req.from_time or "--",
            req.to_time or "--",
            req.location_name or "--",
            req.reason or "--",
            STATUS_LABELS_AR.get(req.status, req.status),
            mgr_obj.full_name if mgr_obj else "الإدارة العليا",
            req.reviewed_by or "بانتظار الاعتماد",
            req.manager_notes or req.admin_notes or "--",
        ]
        for c_idx, val in enumerate(vals, start=1):
            cell = ws4.cell(row=r_idx, column=c_idx, value=val)
            cell.font = BOLD_FONT if c_idx in (1, 2) else DATA_FONT
            cell.alignment = RIGHT if c_idx in (2, 3, 5, 11, 12, 14, 15, 16) else CENTER
            cell.border = CELL_BORDER
            if r_idx % 2 == 0:
                cell.fill = ZEBRA_FILL
            if c_idx == 13:
                if req.status == "APPROVED":
                    cell.fill = GREEN_FILL
                    cell.font = GREEN_FONT
                elif req.status == "PENDING":
                    cell.fill = AMBER_FILL
                    cell.font = AMBER_FONT
                elif req.status == "REJECTED":
                    cell.fill = ROSE_FILL
                    cell.font = ROSE_FONT
                else:
                    cell.fill = SLATE_FILL
                    cell.font = SLATE_FONT

    if hr_requests:
        ws4.auto_filter.ref = f"A4:{last_col_4}{4 + len(hr_requests)}"
    else:
        ws4.merge_cells(f"A5:{last_col_4}5")
        ws4["A5"] = "لا توجد طلبات إجازات أو أذونات أو مأموريات في الفترة المحددة"
        ws4["A5"].alignment = CENTER
        ws4["A5"].font = SUB_FONT

    widths_s4 = [13, 22, 14, 11, 20, 13, 13, 12, 11, 11, 20, 24, 14, 18, 22, 26]
    for i, w in enumerate(widths_s4, start=1):
        ws4.column_dimensions[get_column_letter(i)].width = w

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
