from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import datetime
import json
import random
import traceback

# ------------- FLASK SETUP -------------
app = Flask(__name__)
app.secret_key = "change-this-secret"  # set via env var in production

# ------------- GOOGLE SHEETS SETUP -------------
SVC_FILE = "onetabassesment-866b1b45e270.json"
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name(SVC_FILE, SCOPE)
client = gspread.authorize(creds)

# Open sheets
student_sheet = client.open("StudentsDB").sheet1
question_sheet = client.open("one_tab_assesment").sheet1
response_sheet = client.open("AssessmentQuestions").worksheet("Responses")

# ---------- Safe Google Sheet write helpers ----------
def safe_append(sheet, row_data, sheet_name=""):
    try:
        sheet.append_row(row_data)
        print(f"✅ Data appended to {sheet_name}: {row_data[:4]}...")
    except Exception as e:
        print(f"❌ ERROR appending to {sheet_name}: {e}")
        traceback.print_exc()

def safe_update_cell(sheet, row, col, value, sheet_name=""):
    try:
        sheet.update_cell(row, col, value)
        print(f"✅ Updated {sheet_name} row {row}, col {col} -> {value}")
    except Exception as e:
        print(f"❌ ERROR updating {sheet_name}: {e}")
        traceback.print_exc()

# ------------- UTILITIES -------------
def _headers_to_index_map(ws):
    headers = ws.row_values(1)
    return {h.strip(): i + 1 for i, h in enumerate(headers)}

def _find_student_row(username):
    records = student_sheet.get_all_records()
    for idx, rec in enumerate(records, start=2):
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
        safe_update_cell(student_sheet, row, vio_col, current, sheet_name="StudentsDB")
    return current

def get_questions(department):
    data = question_sheet.get_all_records()
    if not data:
        return []
    df = pd.DataFrame(data)
    df["Department"] = df["Department"].replace("", "Aptitude").fillna("Aptitude")

    apt = df[df["Department"].astype(str).str.strip().str.lower() == "aptitude"]
    dept = df[df["Department"].astype(str).str.strip().str.lower() == str(department).strip().lower()]
    combined = pd.concat([apt, dept], ignore_index=True)

    questions = []
    for _, row in combined.iterrows():
        opts = [str(row["Option1"]).strip(), str(row["Option2"]).strip(),
                str(row["Option3"]).strip(), str(row["Option4"]).strip()]
        random.shuffle(opts)
        questions.append({
            "id": str(row["QID"]).strip(),
            "text": str(row["Question"]).strip(),
            "options": opts,
            "answer": str(row["Answer"]).strip()
        })

    random.shuffle(questions)
    return questions

def calculate_score(answers_dict, department):
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
    open_paths = {"/", "/violation", "/violation-beacon"}
    if request.path.startswith("/static"):
        return
    if request.path in open_paths:
        return
    user = session.get("user")
    if user:
        v = _get_violation_count(user)
        if v > 5:
            session.clear()
            return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
def login():
    students = student_sheet.get_all_records()
    departments = sorted({s.get("Department", "Aptitude") for s in students})

    if request.method == "POST":
        username = request.form.get("Username", "").strip()
        password = request.form.get("Password", "").strip()

        row, rec = _find_student_row(username)
        if not rec or str(rec.get("Password","")).strip() != password:
            return render_template("login.html", departments=departments, duration_min=90, error="Invalid credentials")

        if _is_submitted(username):
            return render_template("login.html", departments=departments, duration_min=90,
                                   error="❌ You have already submitted the test. You cannot login again.")

        vio = _get_violation_count(username)
        if vio > 5:
            return render_template("login.html", departments=departments, duration_min=90,
                                   error="You are blocked due to excessive violations. Contact admin.")

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
    if _is_submitted(session["user"]):
        session.clear()
        return redirect(url_for("login"))
    return render_template("instructions.html", email=session.get("user"),
                           name=session.get("name"), duration_min=90)

@app.route("/exam", methods=["GET", "POST"])
def exam():
    if "user" not in session:
        return redirect(url_for("login"))
    if _is_submitted(session["user"]):
        session.clear()
        return redirect(url_for("login"))

    username = session["user"]
    department = session.get("department", "Aptitude")

    if request.method == "POST":
        answers_json = request.form.get("answers_json") or "{}"
        try:
            answers = json.loads(answers_json)
        except Exception:
            answers = {}

        score = calculate_score(answers, department)
        vio_count = int(session.get("violations", 0))

        row, rec = _find_student_row(username)
        if row:
            headers = _headers_to_index_map(student_sheet)
            vio_col = headers.get("Violations")
            if vio_col:
                safe_update_cell(student_sheet, row, vio_col, vio_count, sheet_name="StudentsDB")

        safe_append(response_sheet, [
            username,
            session.get("roll",""),
            session.get("name",""),
            department,
            session.get("start_time",""),
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            json.dumps(answers, ensure_ascii=False),
            score,
            vio_count
        ], sheet_name="AssessmentQuestions -> Responses")

        row, rec = _find_student_row(username)
        if row:
            headers = _headers_to_index_map(student_sheet)
            sub_col = headers.get("Submitted")
            if sub_col:
                safe_update_cell(student_sheet, row, sub_col, 1, sheet_name="StudentsDB")

        session.clear()
        return render_template("thankyou.html", email=username)

    try:
        qs = get_questions(department)
    except Exception as e:
        return f"Questions error: {e}"

    return render_template("exam.html", questions=qs, duration_min=90,
                           email=username, name=session.get("name", ""),
                           rollno=session.get("roll",""), department=department)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/violation", methods=["POST"])
def violation():
    if "user" not in session:
        return jsonify({"status": "not_logged_in", "violations": 0}), 200
    user = session["user"]
    dept = session.get("department", "Aptitude")
    session["violations"] = int(session.get("violations", 0)) + 1
    count = session["violations"]

    row, rec = _find_student_row(user)
    if row:
        headers = _headers_to_index_map(student_sheet)
        vio_col = headers.get("Violations")
        if vio_col:
            safe_update_cell(student_sheet, row, vio_col, count, sheet_name="StudentsDB")

    if count > 5:
        try:
            answers_json = session.get("answers_json", "{}")
            try:
                answers = json.loads(answers_json)
            except:
                answers = {}
            score = calculate_score(answers, dept)

            safe_append(response_sheet, [
                user,
                session.get("roll",""),
                session.get("name",""),
                dept,
                session.get("start_time",""),
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                json.dumps(answers, ensure_ascii=False),
                score,
                count
            ], sheet_name="AssessmentQuestions -> Responses")

            if row:
                sub_col = headers.get("Submitted")
                if sub_col:
                    safe_update_cell(student_sheet, row, sub_col, 1, sheet_name="StudentsDB")

        except Exception as e:
            print("Auto-submit error:", e)

        session.clear()
        return jsonify({"status": "blocked", "violations": count}), 200

    return jsonify({"status": "ok", "violations": count}), 200

@app.route("/violation-beacon", methods=["POST"])
def violation_beacon():
    try:
        if "user" in session:
            user = session["user"]
            _increment_violation(user)
        return ("", 204)
    except Exception:
        return ("", 204)

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    if "user" in session:
        data = request.get_json(silent=True) or {}
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
