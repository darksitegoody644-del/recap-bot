import os
import asyncio
import tempfile
import subprocess
import logging
import nest_asyncio
nest_asyncio.apply()
import google.generativeai as genai
import edge_tts
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Config
BOT_TOKEN = "8773067396:AAEy8FjC_PweBVvvGwrD-cTMygN-oYTXnOg"
GEMINI_API_KEY = "AIzaSyD60IkRMLTbZDch7slmXXW0qikGXiMVCps"
MYANMAR_VOICE = "my-MM-ThihaNeural"  # Myanmar male voice

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Setup Gemini
genai.configure(api_key=GEMINI_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Video Recap Bot မှ ကြိုဆိုပါတယ်!\n\n"
        "YouTube link, TikTok link, ဒါမှမဟုတ် video file ပို့လိုက်ပါ။\n"
        "မြန်မာအသံ narration နဲ့ recap video ပြန်လုပ်ပေးပါမယ်။"
    )

async def download_video(url, output_dir):
    """Download video using yt-dlp"""
    output_path = os.path.join(output_dir, "input_video.mp4")
    cmd = [
        "yt-dlp",
        "-f", "best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        "--max-filesize", "50M",
        url
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise Exception(f"Download failed: {stderr.decode()}")
    return output_path

async def analyze_video_with_gemini(video_path):
    """Use Gemini to analyze video and create Myanmar recap script"""
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # Upload video file to Gemini
    video_file = genai.upload_file(video_path, mime_type="video/mp4")
    
    # Wait for file to be processed
    import time
    while video_file.state.name == "PROCESSING":
        time.sleep(2)
        video_file = genai.get_file(video_file.name)
    
    if video_file.state.name == "FAILED":
        raise Exception("Gemini video processing failed")
    
    prompt = """ဒီ video ကို ကြည့်ပြီး မြန်မာဘာသာနဲ့ recap/summary script ရေးပေးပါ။
    
    လိုအပ်ချက်:
    - မြန်မာဘာသာ 100% သုံးပါ (technical terms တွေကိုတော့ English အတိုင်း ထားလို့ရတယ်)
    - narration style နဲ့ ရေးပါ (အသံထွက်ဖတ်ဖို့ သင့်တော်အောင်)
    - 30 စက္ကန့် - 1 မိနစ် ကြာလောက်အောင် ရေးပါ
    - video ရဲ့ အဓိက အချက်တွေကို အကျဉ်းချုပ် ပြောပြပါ
    - စိတ်ဝင်စားဖို့ ကောင်းအောင် ရေးပါ
    
    Script ကိုပဲ ပြန်ပေးပါ။ အခြား ရှင်းပြချက် မလိုပါဘူး။"""
    
    response = model.generate_content([video_file, prompt])
    
    # Clean up uploaded file
    genai.delete_file(video_file.name)
    
    return response.text

async def text_to_speech_myanmar(text, output_path):
    """Convert Myanmar text to speech using edge-tts"""
    communicate = edge_tts.Communicate(text, MYANMAR_VOICE)
    await communicate.save(output_path)

async def create_recap_video(video_path, audio_path, output_path):
    """Create recap video with narration audio overlay"""
    # Get audio duration
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    audio_duration = float(result.stdout.strip())
    
    # Create recap video: trim original video to audio length, lower original audio, add narration
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-t", str(audio_duration),
        "-filter_complex",
        "[0:a]volume=0.1[bg];[1:a]volume=1.0[narration];[bg][narration]amix=inputs=2:duration=shortest[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-c:a", "aac",
        "-shortest",
        output_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        # Try without original audio (in case video has no audio)
        cmd2 = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-t", str(audio_duration),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "aac",
            "-shortest",
            output_path
        ]
        process2 = await asyncio.create_subprocess_exec(
            *cmd2, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await process2.communicate()

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube/TikTok URLs"""
    text = update.message.text.strip()
    
    # Check if it's a valid URL
    valid_domains = ["youtube.com", "youtu.be", "tiktok.com", "vm.tiktok.com"]
    is_valid = any(domain in text for domain in valid_domains)
    
    if not is_valid:
        return
    
    status_msg = await update.message.reply_text("⏳ Video download လုပ်နေပါတယ်...")
    
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Step 1: Download video
            video_path = await download_video(text, tmp_dir)
            await status_msg.edit_text("🤖 Video analyze လုပ်နေပါတယ်...")
            
            # Step 2: Analyze with Gemini
            recap_script = await analyze_video_with_gemini(video_path)
            await status_msg.edit_text("🔊 မြန်မာအသံ ဖန်တီးနေပါတယ်...")
            
            # Step 3: TTS
            audio_path = os.path.join(tmp_dir, "narration.mp3")
            await text_to_speech_myanmar(recap_script, audio_path)
            await status_msg.edit_text("🎬 Recap video ဖန်တီးနေပါတယ်...")
            
            # Step 4: Create recap video
            output_path = os.path.join(tmp_dir, "recap_video.mp4")
            await create_recap_video(video_path, audio_path, output_path)
            
            # Step 5: Send video
            await status_msg.edit_text("📤 Video ပို့နေပါတယ်...")
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                with open(output_path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption=f"📝 Recap Script:\n\n{recap_script[:500]}"
                    )
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Video ဖန်တီးရာမှာ အမှားရှိပါတယ်။ ထပ်ကြိုးစားပါ။")
                
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ Error ဖြစ်ပါတယ်: {str(e)[:200]}")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct video file uploads"""
    status_msg = await update.message.reply_text("⏳ Video download လုပ်နေပါတယ်...")
    
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Download video file from Telegram
            video = update.message.video or update.message.document
            file = await context.bot.get_file(video.file_id)
            video_path = os.path.join(tmp_dir, "input_video.mp4")
            await file.download_to_drive(video_path)
            
            await status_msg.edit_text("🤖 Video analyze လုပ်နေပါတယ်...")
            
            # Analyze with Gemini
            recap_script = await analyze_video_with_gemini(video_path)
            await status_msg.edit_text("🔊 မြန်မာအသံ ဖန်တီးနေပါတယ်...")
            
            # TTS
            audio_path = os.path.join(tmp_dir, "narration.mp3")
            await text_to_speech_myanmar(recap_script, audio_path)
            await status_msg.edit_text("🎬 Recap video ဖန်တီးနေပါတယ်...")
            
            # Create recap video
            output_path = os.path.join(tmp_dir, "recap_video.mp4")
            await create_recap_video(video_path, audio_path, output_path)
            
            # Send video
            await status_msg.edit_text("📤 Video ပို့နေပါတယ်...")
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                with open(output_path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption=f"📝 Recap Script:\n\n{recap_script[:500]}"
                    )
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Video ဖန်တီးရာမှာ အမှားရှိပါတယ်။")
                
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ Error ဖြစ်ပါတယ်: {str(e)[:200]}")

def main():
    """Start the bot"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
