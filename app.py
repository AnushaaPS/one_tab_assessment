from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import datetime
import json
import random   

# ------------- FLASK SETUP -------------
app = Flask(__name__)
app.secret_key = "change-this-secret"  # set via env var in production

# ------------- GOOGLE SHEETS SETUP -------------
# Sheets expected:
# 1) StudentsDB (sheet1) columns: Username | Password | Department | RollNo | Violations
# 2) one_tab_assesment (sheet1) columns: QID | Department | Question | Option1 | Option2 | Option3 | Option4 | Answer
# 3) AssessmentQuestions -> worksheet "Responses" columns: Username | RollNo | Department | StartTime | EndTime | AnswersJSON | Score | ViolationsOnSubmit

SVC_FILE = "onetabassesment-866b1b45e270.json"   # your service account file name
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name(SVC_FILE, SCOPE)
client = gspread.authorize(creds)

# Open sheets
student_sheet = client.open("StudentsDB").sheet1
question_sheet = client.open("one_tab_assesment").sheet1
response_sheet = client.open("AssessmentQuestions").worksheet("Responses")


# ------------- UTILITIES -------------
def _headers_to_index_map(ws):
    """Return a dict of header->col_index for a worksheet (1-based)."""
    headers = ws.row_values(1)
    return {h.strip(): i + 1 for i, h in enumerate(headers)}

def _find_student_row(username):
    """Return (row_index, row_dict) for a username; row_index is 1-based."""
    records = student_sheet.get_all_records()
    for idx, rec in enumerate(records, start=2):  # data starts from row 2
        if str(rec.get("Username", "")).strip().lower() == str(username).strip().lower():
            return idx, rec
    return None, None

def _get_student_department(username):
    _, rec = _find_student_row(username)
    return (rec or {}).get("Department", "Aptitude")

def _get_violation_count(username):
    _, rec = _find_student_row(username)
    try:
        return int((rec or {}).get("Violations", 0))
    except Exception:
        return 0
def _is_submitted(username):
    _, rec = _find_student_row(username)
    return str((rec or {}).get("Submitted", "0")).strip() == "1"

def _increment_violation(username):
    row, rec = _find_student_row(username)
    if not row or not rec:
        return 0
    headers = _headers_to_index_map(student_sheet)
    vio_col = headers.get("Violations")
    current = _get_violation_count(username) + 1
    if vio_col:
        student_sheet.update_cell(row, vio_col, current)
    return current

def get_questions(department):
    data = question_sheet.get_all_records()
    if not data:
        return []
    df = pd.DataFrame(data)

    # Fill blank Department cells as "Aptitude"
    df["Department"] = df["Department"].replace("", "Aptitude").fillna("Aptitude")

    # Separate aptitude (common) and department-specific
    apt = df[df["Department"].astype(str).str.strip().str.lower() == "aptitude"]
    dept = df[df["Department"].astype(str).str.strip().str.lower() == str(department).strip().lower()]

    # Combine
    combined = pd.concat([apt, dept], ignore_index=True)

    questions = []
    for _, row in combined.iterrows():
        opts = [
            str(row["Option1"]).strip(),
            str(row["Option2"]).strip(),
            str(row["Option3"]).strip(),
            str(row["Option4"]).strip()
        ]
        random.shuffle(opts)   # shuffle options here
        questions.append({
            "id": str(row["QID"]).strip(),
            "text": str(row["Question"]).strip(),
            "options": opts,
            "answer": str(row["Answer"]).strip()  # for scoring
        })

    random.shuffle(questions)   # shuffle question order
    return questions


def calculate_score(answers_dict, department):
    """
    answers_dict: {qid: selected_option_text}
    Score by exact case-insensitive match against 'Answer' column (text match).
    """
    questions = get_questions(department)
    ans_map = {qid: str(val).strip() for qid, val in (answers_dict or {}).items()}
    score = 0
    for q in questions:
        qid = q["id"]
        correct = str(q["answer"]).strip().lower()
        chosen = str(ans_map.get(qid, "")).strip().lower()
        if chosen and chosen == correct:
            score += 1
    return score


# ------------- ROUTES -------------
@app.before_request
def block_if_exceeded_violations():
    """
    Any request to /exam or violation endpoints should respect the 'max violations' rule.
    If a logged-in user has >5 violations, kill the session and force login.
    """
    # Allow login page, static, etc.
    open_paths = {"/", "/violation", "/violation-beacon"}
    if request.path.startswith("/static"):
        return
    if request.path in open_paths:
        return

    user = session.get("user")
    if user:
        v = _get_violation_count(user)
        if v > 5:
            # Clear session if exceeded
            session.clear()
            return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
def login():
    # Build department list for dropdown
    students = student_sheet.get_all_records()
    departments = sorted({s.get("Department", "Aptitude") for s in students})

    if request.method == "POST":
        username = request.form.get("Username", "").strip()
        password = request.form.get("Password", "").strip()

        row, rec = _find_student_row(username)
        if not rec or str(rec.get("Password","")).strip() != password:
            return render_template("login.html", departments=departments, duration_min=90, error="Invalid credentials")

        # 🚫 Block login if already submitted
        if _is_submitted(username):
            return render_template(
                "login.html",
                departments=departments,
                duration_min=90,
                error="❌ You have already submitted the test. You cannot login again."
            )

        vio = _get_violation_count(username)
        if vio > 5:
            return render_template("login.html", departments=departments, duration_min=90,
                                   error="You are blocked due to excessive violations. Contact admin.")

        # Store session
        session["user"] = username
        session["roll"] = rec.get("RollNo","")
        session["name"] = rec.get("Name", "")  
        session["department"] = rec.get("Department","Aptitude")
        session["start_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return redirect(url_for("instructions"))

    return render_template("login.html", departments=departments, duration_min=90)


@app.route("/instructions")
def instructions():
    if "user" not in session:
        return redirect(url_for("login"))

    # 🚫 If already submitted, force logout and back to login
    if _is_submitted(session["user"]):
        session.clear()
        return redirect(url_for("login"))

    return render_template(
        "instructions.html",
        email=session.get("user"),
        name=session.get("name"),
        duration_min=90
    )



@app.route("/exam", methods=["GET", "POST"])
def exam():
    if "user" not in session:
        return redirect(url_for("login"))

    # 🚫 If already submitted, force logout and back to login
    if _is_submitted(session["user"]):
        session.clear()
        return redirect(url_for("login"))

    username = session["user"]
    department = session.get("department", "Aptitude")

    if request.method == "POST":
        # Receive answers JSON from hidden field
        answers_json = request.form.get("answers_json") or "{}"
        try:
            answers = json.loads(answers_json)
        except Exception:
            answers = {}

        # Calculate score
        score = calculate_score(answers, department)

        # Snapshot current violation count at submit
        #vio_count = _get_violation_count(username)
        # Snapshot current violation count at submit
        vio_count = int(session.get("violations", 0))

        # ✅ Also persist latest violation count to StudentsDB
        row, rec = _find_student_row(username)
        if row:
            headers = _headers_to_index_map(student_sheet)
            vio_col = headers.get("Violations")
            if vio_col:
                student_sheet.update_cell(row, vio_col, vio_count)


        # Store response row
        response_sheet.append_row([
            username,
            session.get("roll",""),
            session.get("name",""),
            department,
            session.get("start_time",""),
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            json.dumps(answers, ensure_ascii=False),
            score,
            vio_count
        ])
        # Mark Submitted
        row, rec = _find_student_row(username)
        if row:
            headers = _headers_to_index_map(student_sheet)
            sub_col = headers.get("Submitted")
            if sub_col:
                student_sheet.update_cell(row, sub_col, 1)

        # Clear session to prevent re-entry without login
        session.clear()
        return render_template("thankyou.html", email=username)

    # GET -> render questions
    try:
        qs = get_questions(department)
    except Exception as e:
        return f"Questions error: {e}"

    return render_template("exam.html",
                           questions=qs,
                           duration_min=90,
                           email=username,
                           name=session.get("name", ""),
                           rollno=session.get("roll",""),
                           department=department)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# Called by client on violations (fetch) to increment and possibly force logout
@app.route("/violation", methods=["POST"])
def violation():
    if "user" not in session:
        return jsonify({"status": "not_logged_in", "violations": 0}), 200

    user = session["user"]
    dept = session.get("department", "Aptitude")

    session["violations"] = int(session.get("violations", 0)) + 1
    count = session["violations"]

    # Persist violation count to StudentsDB
    row, rec = _find_student_row(user)
    if row:
        headers = _headers_to_index_map(student_sheet)
        vio_col = headers.get("Violations")
        if vio_col:
            student_sheet.update_cell(row, vio_col, count)
    # Mark Submitted
    row, rec = _find_student_row(user)
    if row:
        headers = _headers_to_index_map(student_sheet)
        sub_col = headers.get("Submitted")
        if sub_col:
            student_sheet.update_cell(row, sub_col, 1)


    # ✅ Auto-submit if > 5
    if count > 5:
        try:
            answers_json = session.get("answers_json", "{}")
            try:
                answers = json.loads(answers_json)
            except:
                answers = {}

            score = calculate_score(answers, dept)

            response_sheet.append_row([
                user,
                session.get("roll",""),
                session.get("name",""),
                dept,
                session.get("start_time",""),
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                json.dumps(answers, ensure_ascii=False),
                score,
                count
            ])

            # Mark Submitted
            if row:
                sub_col = headers.get("Submitted")
                if sub_col:
                    student_sheet.update_cell(row, sub_col, 1)

        except Exception as e:
            print("Auto-submit error:", e)

        session.clear()
        return jsonify({"status": "blocked", "violations": count}), 200

    return jsonify({"status": "ok", "violations": count}), 200


# Same as /violation but safe to call on pagehide/beforeunload via sendBeacon
@app.route("/violation-beacon", methods=["POST"])
def violation_beacon():
    try:
        if "user" in session:
            user = session["user"]
            _increment_violation(user)
        # send no-body 204 so beacon resolves quickly
        return ("", 204)
    except Exception:
        return ("", 204)


# Optional: heartbeat if you want to persist remaining time or autosave (currently no-op)
@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    if "user" in session:
        data = request.get_json(silent=True) or {}
        # ✅ Store current answers in session
        if "answers" in data:
            session["answers_json"] = json.dumps(data["answers"], ensure_ascii=False)
    return jsonify({"ok": True})

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ------------- MAIN -------------
if __name__ == "__main__":
    app.run(debug=True)
