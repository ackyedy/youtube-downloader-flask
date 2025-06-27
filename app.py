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

# 必要なライブラリのチェック（Render環境では通常不要ですが、ローカル実行時のために残します）
missing_libs = []
try:
    import flask
except ImportError:
    missing_libs.append('Flask')
try:
    import yt_dlp
except ImportError:
    missing_libs.append('yt-dlp')
try:
    import mutagen
except ImportError:
    missing_libs.append('mutagen')
if missing_libs:
    print(f"ERROR: 必要なライブラリがインストールされていません: {', '.join(missing_libs)}")
    sys.exit(1)

from flask import Flask, render_template, request, send_file, redirect, url_for, flash, after_this_request

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

# フォルダ定義
DOWNLOAD_FOLDER = "downloads"
THUMB_FOLDER = Path(__file__).resolve().parent / "static" / "thumbnail"
THUMB_FOLDER.mkdir(parents=True, exist_ok=True)
Path(DOWNLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

# 終了時クリーンアップ
@atexit.register
def cleanup_temp_dirs():
    for folder in [DOWNLOAD_FOLDER, THUMB_FOLDER]:
        if os.path.exists(folder):
            try:
                shutil.rmtree(folder)
                logger.info(f"Cleaned up directory: {folder}")
            except Exception as e:
                logger.error(f"Error during cleanup of {folder}: {e}")

# FFmpegチェック

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')
    except Exception:
        return None

ffmpeg_path = check_ffmpeg()
if not ffmpeg_path:
    logger.warning("ffmpeg not found. Thumbnail embedding may fail.")

# サニタイズ

def sanitize_filename(filename, max_length=150):
    if converter:
        filename = converter.do(filename)
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = re.sub(r'[^\w぀-ヿ㐀-䶿一-鿿\-. ]', '', filename)
    return filename.strip()[:max_length]

# 数字整形

def format_number(num):
    if num is None: return "不明"
    try:
        num = int(num)
        if num >= 1_000_000_000: return f"{num/1_000_000_000:.1f}B"
        if num >= 1_000_000: return f"{num/1_000_000:.1f}M"
        if num >= 1_000: return f"{num/1_000:.1f}K"
        return str(num)
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
    cmd = f'ffmpeg -i "{thumbnail_path}" -y "{jpg_path}"'
    os.system(cmd)
    if os.path.exists(jpg_path):
        os.remove(thumbnail_path)
        return jpg_path
    return thumbnail_path

# 手動埋め込み

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

# 進捗フック

def download_progress_hook(d):
    if d['status'] == 'downloading':
        pass
    elif d['status'] == 'finished':
        logger.info('Download finished for %s, now post-processing.', d['filename'])

# フォーマット一覧

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
        fmt = request.form.get("format")
        if not url:
            flash("URLを入力してください。", "error")
            return redirect(url_for("index"))
        if not URL_PATTERN.match(url):
            flash("対応している動画サイトのURLを入力してください。", "error")
            return redirect(url_for("index"))

        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_FOLDER)
        try:
            ydl_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'restrictfilenames': False,
                'addmetadata': True,
                'nopart': True,
                'noplaylist': True,
                'ignoreerrors': False,
                'noprogress': True,
                'logger': logger,
                'ffmpeg_location': ffmpeg_path,
                'writethumbnail': True,
                'postprocessors': [],
                'progress_hooks': [download_progress_hook],
            }
            expected_ext = 'mp4'
            if fmt.startswith('video_'):
                height = int(fmt.split('_')[1])
                ydl_opts['format'] = f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"
                ydl_opts['merge_output_format'] = 'mp4'
            else:
                expected_ext = fmt
                ydl_opts['format'] = 'bestaudio/best'
                ydl_opts['outtmpl'] = os.path.join(temp_dir, '%(title)s.%(id)s.%(ext)s')
                ydl_opts['postprocessors'] += [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': fmt, 'preferredquality': '320'},
                    {'key': 'FFmpegMetadata'},
                ]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            # ダウンロードされたファイルの取得
            downloaded_filepath = None
            thumbnail_filepath = None
            for fname in os.listdir(temp_dir):
                full = os.path.join(temp_dir, fname)
                if fname.endswith(f".{expected_ext}"):
                    downloaded_filepath = full
                elif any(fname.lower().endswith(ext) for ext in ['.jpg','.png','.webp','.jpeg']):
                    thumbnail_filepath = full

            if not downloaded_filepath:
                raise Exception("ダウンロードされたファイルが見つかりませんでした。")

            # サムネイル処理
            local_thumb_url = None
            if thumbnail_filepath:
                thumb_conv = convert_to_jpg(thumbnail_filepath)
                final_thumb_name = sanitize_filename(info.get('title','thumb')) + os.path.splitext(thumb_conv)[1]
                dest_thumb = THUMB_FOLDER / final_thumb_name
                shutil.move(thumb_conv, dest_thumb)
                local_thumb_url = url_for('static', filename=f'thumbnail/{final_thumb_name}')
                embed_thumbnail_manual(downloaded_filepath, str(dest_thumb))
            else:
                if info.get('thumbnail'):
                    local_thumb_url = info.get('thumbnail')

            # ファイル移動とメタデータ整形
            sanitized = sanitize_filename(info.get('title','file'))
            final_name = f"{sanitized}.{expected_ext}"
            final_path = os.path.join(DOWNLOAD_FOLDER, final_name)
            shutil.move(downloaded_filepath, final_path)

            metadata = {
                'title': info.get('title','不明'),
                'uploader': info.get('uploader','不明'),
                'views': format_number(info.get('view_count')),
                'upload_date': format_date(info.get('upload_date')),
                'duration': info.get('duration_string','不明'),
                'filename': final_name,
                'thumbnail': local_thumb_url,
                'ext': expected_ext
            }

            flash("ダウンロードが完了しました！", "success")
            return render_template('index.html', FORMATS=FORMATS, ffmpeg_available=ff_available, metadata=metadata)

        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            logger.error(f"yt-dlp Download Error: {msg}")
            if "Sign in to confirm you’re not a bot" in msg:
                flash("ダウンロードエラー: YouTubeのボット対策によりブロックされた可能性があります。", "error")
            else:
                flash(f"ダウンロードエラー: {msg[:100]}", "error")
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            flash(f"予期しないエラーが発生しました: {str(e)[:100]}", "error")
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

    safe_filename = filename
    @after_this_request
    def remove_file(response):
        try:
            os.remove(filepath)
            logger.info(f"File removed after download: {filepath}")
        except Exception as e:
            logger.error(f"Error removing file: {e}")
        return response

    return send_file(filepath, as_attachment=True, download_name=safe_filename, mimetype='application/octet-stream')

@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', error='お探しのページは見つかりませんでした。'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', error='サーバー内部でエラーが発生しました。'), 500

# サーバー起動
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
