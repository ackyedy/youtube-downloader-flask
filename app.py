#!/usr/bin/env python3
import sys
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
import yt_dlp
import os
import atexit
import tempfile
import logging
from datetime import datetime
import re
try:
    from pykakasi import kakasi
    kks = kakasi()
    kks.setMode("J", "a")
    converter = kks.getConverter()
except ImportError:
    converter = None
import shutil
import sys
from pathlib import Path

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_fallback_secret_key_for_development")

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DOWNLOAD_FOLDER = "downloads"
THUMB_FOLDER = Path(__file__).resolve().parent / "static" / "thumbnail"
THUMB_FOLDER.mkdir(parents=True, exist_ok=True)
THUMB_FOLDER.mkdir(parents=True, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

# FFmpegが利用可能かチェック
def check_ffmpeg():
    ffmpeg_path = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ffmpeg_path:
        logger.error("ffmpeg not found. Thumbnail embedding will fail.")
        flash("警告: ffmpegがインストールされていません。サムネイルの埋め込みに失敗する可能性があります。", "warning")
    return ffmpeg_path

# サーバー終了時のクリーンアップ
def cleanup_files():
    try:
        for folder in [DOWNLOAD_FOLDER, THUMB_FOLDER]:
            for filename in os.listdir(folder):
                filepath = os.path.join(folder, filename)
                if os.path.isfile(filepath) or os.path.islink(filepath):
                    os.remove(filepath)
                elif os.path.isdir(filepath):
                    shutil.rmtree(filepath)
            logger.info(f"Cleanup completed: All files in '{folder}' folder removed.")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

atexit.register(cleanup_files)

FORMATS = [
    ("video_2160", "4K (2160p)", "🎬"),
    ("video_1440", "2K (1440p)", "📹"),
    ("video_1080", "Full HD (1080p)", "🎥"),
    ("video_720",  "HD (720p)", "📺"),
    ("video_480",  "480p", "📱"),
    ("video_360",  "360p", "📱"),
    ("mp3",  "MP3 (音声)", "🎵"),
    ("m4a",  "M4A (音声)", "🎧"),
    ("opus", "Opus (音声)", "🔊"),
    ("wav",  "WAV (音声)", "🎶"),
]

def sanitize_filename(filename):
    """ファイル名を安全な形式に変換（日本語対応強化版）"""
    # 不正な文字を除去
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # 日本語とASCII文字を保持
    filename = re.sub(r'[^\w\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\-. ]', '', filename)
    return filename.strip()[:150]

def format_number(num):
    if num is None: return "不明"
    try:
        num = int(num)
        if num >= 1_000_000_000: return f"{num / 1_000_000_000:.1f}B"
        if num >= 1_000_000: return f"{num / 1_000_000:.1f}M"
        if num >= 1_000: return f"{num / 1_000:.1f}K"
        return str(num)
    except (ValueError, TypeError):
        return "不明"

def format_date(date_str):
    if not date_str: return "不明"
    try:
        return datetime.strptime(date_str, "%Y%m%d").strftime("%Y年%m月%d日")
    except ValueError:
        return date_str

def convert_to_jpg(thumbnail_path):
    """サムネイルをJPG形式に変換（埋め込み互換性向上）"""
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        return None
        
    base, ext = os.path.splitext(thumbnail_path)
    if ext.lower() == '.jpg':
        return thumbnail_path
        
    jpg_path = base + '.jpg'
    try:
        # 変換コマンド実行
        ffmpeg_cmd = f'ffmpeg -i "{thumbnail_path}" -y "{jpg_path}"'
        os.system(ffmpeg_cmd)
        
        if os.path.exists(jpg_path):
            os.remove(thumbnail_path)  # 元ファイルを削除
            return jpg_path
    except Exception as e:
        logger.error(f"Thumbnail conversion error: {e}")
    
    return thumbnail_path


def embed_thumbnail_manual(file_path, thumbnail_path):
    """音声・動画ファイルにカバー画像を埋め込む"""
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
            audio.tags.add(
                APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=img.read()
                )
            )
        audio.save(v2_version=3)
    elif ext in ['.m4a', '.mp4']:
        from mutagen.mp4 import MP4, MP4Cover
        mp4 = MP4(file_path)
        with open(thumbnail_path, 'rb') as img:
            cover = MP4Cover(img.read(), imageformat=MP4Cover.FORMAT_JPEG)
            mp4.tags['covr'] = [cover]
        mp4.save()

@app.route("/", methods=["GET", "POST"])
def index():
    ffmpeg_available = check_ffmpeg() is not None
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        fmt = request.form.get("format")

        if not url:
            flash("URLを入力してください。", "error")
            return redirect(url_for("index"))

        # URLバリデーション: 主要動画配信サイトを許可
        video_sites = [
            r"(youtube\.com|youtu\.be|music\.youtube\.com)",
            r"vimeo\.com",
            r"(tiktok\.com|vm\.tiktok\.com)",
            r"(twitter\.com|x\.com)",
            r"nicovideo\.jp",
            # 他の動画サイトを必要に応じて追加
        ]
        pattern = rf"^(https?://)?(www\.)?({'|'.join(video_sites)})/.+$"
        if not re.match(pattern, url):
            flash("対応している動画サイトのURLを入力してください。", "error")
            return redirect(url_for("index"))

        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_FOLDER)
        ffmpeg_path = check_ffmpeg()

        if not ffmpeg_path and fmt.startswith("video_"):
            flash("ffmpegが見つかりません。動画処理にはffmpegが必要です。", "error")
            logger.error("ffmpeg not found for video processing.")
            shutil.rmtree(temp_dir)
            return redirect(url_for("index"))

        ydl_opts = {
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'restrictfilenames': False,  # 日本語ファイル名を許可
            'addmetadata': True,
            'nopart': True,
            'noplaylist': True,
            'ignoreerrors': False,
            'noprogress': True,
            'logger': logger,
            'ffmpeg_location': ffmpeg_path,
            'writethumbnail': True,
            'postprocessors': [],
        }

        expected_ext = "mp4"

        if fmt.startswith("video_"):
            height = int(fmt.split("_")[1])
            ydl_opts['format'] = f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'
            ydl_opts['merge_output_format'] = 'mp4'
        else:
            expected_ext = fmt
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['outtmpl'] = os.path.join(temp_dir, '%(title)s.%(id)s.%(ext)s')
            ydl_opts['postprocessors'].extend([
                {'key': 'FFmpegExtractAudio', 'preferredcodec': fmt, 'preferredquality': '320'},
                {'key': 'FFmpegMetadata'},
            ])

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                sanitized_title = sanitize_filename(info.get("title", "unknown"))
                downloaded_filepath = None
                thumbnail_filepath = None
                # 一時フォルダ内のファイルを検索
                for filename in os.listdir(temp_dir):
                    full_path = os.path.join(temp_dir, filename)
                    # メディアファイルを検索
                    if filename.endswith(f".{expected_ext}"):
                        downloaded_filepath = full_path
                    # サムネイルファイルを検索
                    elif any(filename.lower().endswith(ext) for ext in ['.jpg', '.webp', '.png', '.jpeg']):
                        thumbnail_filepath = full_path
                if not downloaded_filepath:
                    raise Exception("ダウンロードされたファイルが見つかりませんでした。")
                # サムネイル処理
                local_thumbnail_url = None
                if thumbnail_filepath:
                    # WebPなど非互換形式をJPGに変換
                    thumbnail_filepath = convert_to_jpg(thumbnail_filepath)
                    thumb_ext = os.path.splitext(thumbnail_filepath)[1]
                    thumb_title = sanitized_title
                    if converter and re.search(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]', sanitized_title):
                        thumb_title = converter.do(sanitized_title)
                    thumb_title = re.sub(r'\s+', '_', thumb_title)
                    thumb_final_name = f"{thumb_title}_{info.get('id')}{thumb_ext}"
                    thumb_dest_path = os.path.join(THUMB_FOLDER, thumb_final_name)
                    if os.path.exists(thumb_dest_path):
                        os.remove(thumb_dest_path)
                    shutil.move(thumbnail_filepath, thumb_dest_path)
                    local_thumbnail_url = url_for('static', filename=f'thumbnail/{thumb_final_name}')
                else:
                    thumbnail_url = info.get('thumbnail')
                    if thumbnail_url:
                        local_thumbnail_url = thumbnail_url
                    logger.warning("Thumbnail file not found")
                # ファイル名の最終サニタイズ
                final_name = f"{sanitized_title}.{expected_ext}"
                final_path = os.path.join(DOWNLOAD_FOLDER, final_name)
                shutil.move(downloaded_filepath, final_path)
                # 手動でサムネイル埋め込み
                if thumbnail_filepath:
                    embed_thumbnail_manual(final_path, thumb_dest_path)
                metadata = {
                    "title": info.get("title", "不明"),
                    "uploader": info.get("uploader", "不明"),
                    "views": format_number(info.get("view_count")),
                    "upload_date": format_date(info.get("upload_date")),
                    "duration": info.get("duration_string", "不明"),
                    "filename": final_name,
                    "thumbnail": local_thumbnail_url,
                    "ext": expected_ext
                }
                flash("ダウンロードが完了しました！", "success")
                return render_template("index.html", FORMATS=FORMATS, metadata=metadata, ffmpeg_available=ffmpeg_available)
        except yt_dlp.utils.DownloadError as e:
            err_msg = str(e).split(':')[-1].strip() or "不明なエラー"
            flash(f"ダウンロードエラー: {err_msg}", "error")
            logger.error(f"yt-dlp Download Error: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error during download process: {e}", exc_info=True)
            flash(f"予期しないエラーが発生しました: {str(e)[:100]}", "error")
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
        return redirect(url_for("index"))
    return render_template("index.html", FORMATS=FORMATS, ffmpeg_available=ffmpeg_available)

@app.route("/download/<filename>")
def download_file(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    if not os.path.exists(filepath):
        flash("ファイルが見つかりません。", "error")
        return redirect(url_for("index"))

    # 安全なファイル名でダウンロード（UTF-8対応）
    safe_filename = filename
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(filepath)
            logger.info(f"File removed after download: {filepath}")
        except Exception as e:
            logger.error(f"Error removing file after download: {e}")
        return response

    return send_file(
        filepath,
        as_attachment=True,
        download_name=safe_filename,
        mimetype='application/octet-stream'
    )

@app.errorhandler(404)
def not_found_error(error):
    return render_template("error.html", error="お探しのページは見つかりませんでした。"), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template("error.html", error="サーバー内部でエラーが発生しました。"), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)