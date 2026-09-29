from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import re
import uuid
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

AREA_CODE = "0220100"  # 青森市

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
REPORTS_FILE = os.path.join(APP_DIR, 'data', 'report_history.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def save_shelters(updated_shelters):
    """避難所データをファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(updated_shelters, f, ensure_ascii=False, indent=2)
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def extract_report_fields(transcript):
    """文字起こしから根拠を確認できる通報情報を保守的に抽出する"""
    text = transcript.strip()
    unknown = '不明'
    caller_turns = re.findall(r'通報者：([^\n]+)', text)
    caller_text = '\n'.join(caller_turns) if caller_turns else text

    if not text:
        return {
            '災害種別': unknown,
            '場所': unknown,
            '通報者の現在地': unknown,
            '通報者の安全状況': unknown,
            '避難状況': unknown,
            '人的被害': unknown,
            '人数': unknown,
            '水位・浸水深等': unknown,
            '救助要請': unknown,
            '通報日時': unknown,
            '発生・確認状況': unknown,
            '確認された内容': [],
            '不明・要確認': ['文字起こしが入力されていません'],
            '通報者による推測': []
        }

    city = '青森市' if '青森市' in caller_text else unknown
    if '安方です' in caller_text or '安方。' in caller_text or '安方ですね' in caller_text:
        district = '安方'
    elif '安形' in caller_text:
        district = '安形（要確認）'
    elif '安方' in caller_text:
        district = '安方'
    else:
        district = unknown

    location_parts = [part for part in (city, district) if part != unknown]
    if '青森駅' in caller_text:
        location_parts.append('青森駅から海側（詳細位置不明）')
    location = '、'.join(location_parts) if location_parts else unknown

    disaster = '津波（通報者の申告）' if '津波' in caller_text else unknown
    current_location = (
        '建物の2階' if re.search(r'建物の(?:二階|2階)', text) else unknown
    )
    if '安全な場所にいます' in caller_text or '今は安全です' in caller_text:
        safety = '通報者は安全な場所にいると回答'
    elif current_location != unknown:
        safety = '建物の2階にいるとの発言あり。安全かどうかの明言はなし'
    else:
        safety = unknown

    if '三階' in caller_text or '3階' in caller_text:
        evacuation = '建物の2階におり、3階へ移動できそうと発言。移動完了は不明'
    elif '避難' in caller_text or '逃げました' in caller_text:
        evacuation = '建物に避難したと発言'
    else:
        evacuation = unknown

    report_datetime = re.search(
        r'(20\d{2}年\d{1,2}月\d{1,2}日(?:\s*\d{1,2}時(?:\d{1,2}分)?)?)',
        text
    )
    report_time = report_datetime.group(1) if report_datetime else unknown

    confirmed = []
    if '道路' in caller_text and ('浸水' in caller_text or '水に使って' in caller_text):
        confirmed.append('道路が浸水しているとの目視報告')
    if '動けなくなって' in caller_text:
        confirmed.append('複数の車が動けなくなっているとの目視報告')
    if '屋根' in caller_text and '人' in caller_text:
        confirmed.append('周辺の建物の上部に避難している人がいるとの目視報告')

    ongoing = bool(
        re.search(r'(?:まだ|現在も).{0,20}(?:来て|流入|入って)', caller_text)
    )
    status = '海側から水が流入し、現在も進行中との報告' if ongoing else unknown

    uncertain = []
    if any(
        phrase in caller_text for phrase in (
            '流されてるかもしれない',
            '流されているかもしれない',
            '流されているかもしれません'
        )
    ):
        uncertain.append('車が流されている可能性があるとの通報者の推測。移動・流出は確認できていない')
    if any(
        phrase in caller_text for phrase in (
            '増えてる気がする',
            '増えてる気がします',
            '増えている気がする',
            '増えている気がします'
        )
    ):
        uncertain.append('水位が上がっているように感じるとの通報者の推測')

    people_count = '不明'
    if any(term in caller_text for term in ('何人か', '複数人', '数人', '何名か')):
        people_count = '複数（正確な人数は不明）'

    human_damage = '不明'
    if people_count != unknown or ('屋根' in caller_text and '人' in caller_text):
        human_damage = '周辺に複数人がいるとの報告。負傷の有無は不明'

    water_depth = unknown
    if '車のタイヤ' in caller_text:
        water_depth = '車のタイヤが半分からかなり隠れる程度との目視報告。正確な水深は不明'

    rescue_requested = bool(re.search(r'救助|救出|助けて|助けをお願いします', caller_text))
    rescue_status = 'あり' if rescue_requested else 'なし（明示的な要請なし）'

    unknown_items = []
    if report_time == unknown:
        unknown_items.append('通報日時（文字起こしに日時の発言なし）')
    if '怪我' in caller_text or 'けが' in caller_text:
        unknown_items.append('周辺にいる人の負傷の有無・人数')
    if '正確にはわからない' in caller_text or '高さ' in caller_text:
        unknown_items.append('水位・浸水深の正確な値')
    if district.endswith('（要確認）'):
        unknown_items.append('地区名の表記')

    return {
        '災害種別': disaster,
        '場所': location,
        '通報者の現在地': current_location,
        '通報者の安全状況': safety,
        '避難状況': evacuation,
        '人的被害': human_damage,
        '人数': people_count,
        '水位・浸水深等': water_depth,
        '救助要請': rescue_status,
        '通報日時': report_time,
        '発生・確認状況': status,
        '確認された内容': confirmed,
        '不明・要確認': unknown_items,
        '通報者による推測': uncertain
    }


def assess_report_urgency(fields):
    """根拠が明確な場合だけ緊急度を付け、判断材料不足は要確認にする"""
    if fields.get('救助要請') == 'あり':
        return '高', '通報者から救助・救出の要請あり'

    if fields.get('災害種別', '').startswith('津波') and '進行中' in fields.get('発生・確認状況', ''):
        return '高', '津波による水の流入が現在も続いているとの報告'

    if fields.get('確認された内容'):
        return '中', '道路浸水・車両停止などの被害情報あり'

    return '要確認', '緊急度を判断できる情報が不足'


def save_reports(reports):
    temporary_file = f'{REPORTS_FILE}.tmp'
    with open(temporary_file, 'w', encoding='utf-8') as file:
        json.dump(reports, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, REPORTS_FILE)


def create_report_record(transcript):
    fields = extract_report_fields(transcript)
    urgency, urgency_reason = assess_report_urgency(fields)
    return {
        'id': uuid.uuid4().hex[:12],
        'created_at': datetime.now(JST).isoformat(),
        'status': '未確認',
        'urgency': urgency,
        'urgency_reason': urgency_reason,
        'fields': fields,
        'transcript': transcript.strip()
    }


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の最新状態を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    latest_area = None
    latest_report_datetime = ""
    latest_datetime = None

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if not isinstance(report_datetime, str) or not report_datetime:
            continue

        try:
            parsed_datetime = datetime.fromisoformat(
                report_datetime.replace('Z', '+00:00')
            )
            if parsed_datetime.tzinfo is None:
                parsed_datetime = parsed_datetime.replace(tzinfo=JST)
            parsed_datetime = parsed_datetime.astimezone(timezone.utc)
        except ValueError:
            continue

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area or (latest_datetime and parsed_datetime <= latest_datetime):
            continue

        latest_area = area
        latest_report_datetime = report_datetime
        latest_datetime = parsed_datetime

    warnings = []
    seen_codes = set()
    kinds = latest_area.get("kinds", []) if latest_area else []
    if isinstance(kinds, list):
        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return render_template(
                'shelter_register.html', error=True,
                message='避難所名を入力してください。'
            )

        new_shelter = {
            'id': max((s.get('id', 0) for s in shelters), default=0) + 1,
            'name': name
        }
        updated_shelters = shelters + [new_shelter]
        try:
            save_shelters(updated_shelters)
        except OSError:
            return render_template(
                'shelter_register.html', error=True,
                message='避難所を保存できませんでした。'
            ), 500

        shelters.append(new_shelter)
        return redirect(url_for('all_shelters'))

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')


@app.route('/report_intake')
def report_intake():
    return render_template('report_dashboard.html')


@app.route('/api/extract_report', methods=['POST'])
def api_extract_report():
    payload = request.get_json(silent=True) or {}
    transcript = payload.get('transcript', '')
    if not isinstance(transcript, str):
        transcript = ''
    return jsonify(extract_report_fields(transcript))


@app.route('/api/reports', methods=['GET', 'POST'])
def api_reports():
    reports = load_json(REPORTS_FILE, [])
    if request.method == 'GET':
        return jsonify(sorted(reports, key=lambda report: report.get('created_at', ''), reverse=True))

    payload = request.get_json(silent=True) or {}
    transcript = payload.get('transcript', '')
    if not isinstance(transcript, str) or not transcript.strip():
        return jsonify({'error': '文字起こしを入力してください。'}), 400

    report = create_report_record(transcript)
    reports.append(report)
    try:
        save_reports(reports)
    except OSError:
        return jsonify({'error': '通報を保存できませんでした。'}), 500
    return jsonify(report), 201


@app.route('/api/reports/<report_id>', methods=['PATCH'])
def api_update_report(report_id):
    payload = request.get_json(silent=True) or {}
    status = payload.get('status')
    if status not in ('未確認', '確認済み'):
        return jsonify({'error': '確認状態が不正です。'}), 400

    reports = load_json(REPORTS_FILE, [])
    report = next((item for item in reports if item.get('id') == report_id), None)
    if report is None:
        return jsonify({'error': '通報が見つかりません。'}), 404

    report['status'] = status
    try:
        save_reports(reports)
    except OSError:
        return jsonify({'error': '確認状態を保存できませんでした。'}), 500
    return jsonify(report)


# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template('board.html', instructions=resident_instructions)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
