#!/usr/bin/env python3
import sys
import os
import atexit
import tempfile
import logging
import re
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, send_file, redirect, url_for, flash, after_this_request

# 必要なライブラリのチェック
missing_libs = []
for lib, name in [("flask", 'Flask'), ("yt_dlp", 'yt-dlp'), ("mutagen", 'mutagen')]:
    try:
        __import__(lib)
    except ImportError:
        missing_libs.append(name)
if missing_libs:
    print(f"ERROR: 必要なライブラリがインストールされていません: {', '.join(missing_libs)}")
    sys.exit(1)

# 日本語ファイル名のローマ字変換
try:
    from pykakasi import kakasi
    kks = kakasi()
    kks.setMode("J", "a")
    converter = kks.getConverter()
except ImportError:
    converter = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_fallback_secret_key_for_development")

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# フォルダ設定
DOWNLOAD_FOLDER = "downloads"
THUMB_FOLDER = Path(__file__).resolve().parent / "static" / "thumbnail"
THUMB_FOLDER.mkdir(parents=True, exist_ok=True)
Path(DOWNLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

# アプリ終了時のクリーンアップ
@atexit.register
def cleanup_temp_dirs():
    for folder in [DOWNLOAD_FOLDER, THUMB_FOLDER]:
        if os.path.exists(folder):
            try:
                shutil.rmtree(folder)
                logger.info(f"Cleaned up directory: {folder}")
            except Exception as e:
                logger.error(f"Error cleaning up {folder}: {e}")

# FFmpegチェック

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')
    except Exception:
        return None

ffmpeg_path = check_ffmpeg()
if not ffmpeg_path:
    logger.warning("ffmpeg not found. Thumbnail or embed may fail.")

# ファイル名サニタイズ

def sanitize_filename(filename, max_length=150):
    if converter:
        filename = converter.do(filename)
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = re.sub(r'[^\w぀-ヿ㐀-䶿一-鿿\-. ]', '', filename)
    return filename.strip()[:max_length]

# 再生数整形

def format_number(num):
    if num is None: return "不明"
    try:
        n = int(num)
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f}B"
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000: return f"{n/1_000:.1f}K"
        return str(n)
    except:
        return "不明"

# 日付整形

def format_date(date_str):
    if not date_str: return "不明"
    try:
        return datetime.strptime(date_str, "%Y%m%d").strftime("%Y年%m月%d日")
    except:
        return date_str

# サムネイル変換

def convert_to_jpg(thumbnail_path):
    if not thumbnail_path or not os.path.exists(thumbnail_path): return None
    base, ext = os.path.splitext(thumbnail_path)
    if ext.lower() == '.jpg': return thumbnail_path
    jpg_path = base + '.jpg'
    os.system(f'ffmpeg -i "{thumbnail_path}" -y "{jpg_path}"')
    if os.path.exists(jpg_path):
        os.remove(thumbnail_path)
        return jpg_path
    return thumbnail_path

# 手動サムネイル埋め込み

def embed_thumbnail_manual(file_path, thumbnail_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.mp3':
        from mutagen.id3 import ID3, APIC, error
        from mutagen.mp3 import MP3
        audio = MP3(file_path, ID3=ID3)
        try:
            audio.add_tags()
        except error:
            pass
        with open(thumbnail_path, 'rb') as img:
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img.read()))
        audio.save(v2_version=3)
    elif ext in ['.m4a', '.mp4']:
        from mutagen.mp4 import MP4, MP4Cover
        mp4 = MP4(file_path)
        with open(thumbnail_path, 'rb') as img:
            cover = MP4Cover(img.read(), imageformat=MP4Cover.FORMAT_JPEG)
            mp4.tags['covr'] = [cover]
        mp4.save()

# ダウンロード進捗

def download_progress_hook(d):
    if d['status'] == 'finished':
        logger.info(f"Download finished: {d.get('filename')}")

# 対応フォーマット一覧

FORMATS = [
    ("video_2160", "4K (2160p)", "🎬"),
    ("video_1440", "2K (1440p)", "📹"),
    ("video_1080", "Full HD (1080p)", "🎥"),
    ("video_720",  "HD (720p)", "📺"),
    ("video_480",  "480p", "📱"),
    ("video_360",  "360p", "📱"),
    ("mp3",        "MP3 (音声)", "🎵"),
    ("m4a",        "M4A (音声)", "🎧"),
    ("opus",       "Opus (音声)", "🔊"),
    ("wav",        "WAV (音声)", "🎶"),
]

# URLバリデーション
VIDEO_SITES = [r"youtube\.com|youtu\.be", r"vimeo\.com", r"tiktok\.com", r"twitter\.com|x\.com", r"nicovideo\.jp"]
URL_PATTERN = re.compile(rf"^(https?://)?(www\.)?({'|'.join(VIDEO_SITES)})/.+$")

@app.route("/", methods=["GET", "POST"])
def index():
    ff_available = bool(ffmpeg_path)
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        fmt = request.form.get("format_type")
        if not url:
            flash("URLを入力してください。", "error")
            return redirect(url_for("index"))
        if not URL_PATTERN.match(url):
            flash("対応している動画サイトのURLを入力してください。", "error")
            return redirect(url_for("index"))

        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_FOLDER)
        try:
            # yt-dlp オプション
            ydl_opts = {
                'format': None,  # 以下で指定
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'restrictfilenames': False,
                'addmetadata': True,
                'nopart': False,
                'noplaylist': True,
                'ignoreerrors': False,
                'logger': logger,
                'ffmpeg_location': ffmpeg_path,
                'writethumbnail': True,
                'postprocessors': [],
                'progress_hooks': [download_progress_hook],
                # 基本の対策
                'sleep_interval': 5,
                'max_sleep_interval': 10,
                'extractor_retries': 3,
                'source_address': '0.0.0.0',
                'http_headers': {
                    'User-Agent': os.environ.get('USER_AGENT', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'),
                    'Accept-Language': 'ja-JP,ja;q=0.9',
                },
                'cookiefile': os.environ.get('COOKIE_FILE', 'cookies.txt'),
                # 追加対策：分割DL制御
                'ratelimit': 500_000,
                'skip_unavailable_fragments': True,
                'hls_prefer_native': True,
                # クライアント偽装
                'extractor_args': {
                    'youtube': {'player_client': 'android'}
                },
                # Proxy 例
                # 'proxy': os.environ.get('YTDLP_PROXY', ''),
            }
            # フォーマット設定
            if fmt and fmt.startswith('video_'):
                height = int(fmt.split('_')[1])
                ydl_opts['format'] = f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"
                ydl_opts['merge_output_format'] = 'mp4'
                expected_ext = 'mp4'
            else:
                expected_ext = fmt or 'mp3'
                ydl_opts['format'] = 'bestaudio/best'
                ydl_opts['outtmpl'] = os.path.join(temp_dir, '%(title)s.%(id)s.%(ext)s')
                ydl_opts['postprocessors'] += [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': expected_ext, 'preferredquality': '320'},
                    {'key': 'FFmpegMetadata'},
                ]

            # 実行
            with __import__('yt_dlp').YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            # ...以降は既存ロジック（ファイル発見〜移動〜埋め込み〜metadata生成）
            # 同様に処理されます

        except Exception as e:
            logger.error(f"下载错误: {e}")
            flash(f"エラーが発生しました: {str(e)[:100]}", "error")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return redirect(url_for('index'))
    return render_template('index.html', FORMATS=FORMATS, ffmpeg_available=ff_available)

@app.route('/download/<filename>')
def download_file(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    if not os.path.exists(filepath):
        flash('ファイルが見つかりません。', 'error')
        return redirect(url_for('index'))

    @after_this_request
    def remove_file(response):
        try:
            os.remove(filepath)
        except:
            pass
        return response

    return send_file(filepath, as_attachment=True, download_name=filename)

@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', error='お探しのページは見つかりませんでした。'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', error='サーバー内部でエラーが発生しました。'), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
