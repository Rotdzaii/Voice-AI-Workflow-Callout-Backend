# Usage: .\scripts\set_env.ps1
# Loads environment variables for the current PowerShell session.
# Edit values as needed.

# Required
$env:GEMINI_KEY = "AIzaSyCINXrXwmjjPul-4BHGrbnNstemeDMfwKo"
$env:RAW_INPUT_FILE = "C:\Users\Admin\MyProject\va\data\data_cleaned_chunked.csv"
$env:CLEAN_CHUNK_FILE = "C:\Users\Admin\MyProject\va\data\data_cleaned_chunked.csv"
$env:CHROMA_DB_PATH = ".\mitek_chroma_db"
$env:LOG_FILE_PATH = "rag_logs.csv"

# Optional performance/verbosity
$env:MAX_RETRIEVED_CHUNKS = "3"
$env:BATCH_SIZE_GPU = "32"
$env:RAG_VERBOSE = "1"

# Optional TTS
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\Users\Admin\MyProject\va\data\gen-lang-client-0679496469-3f8798e4613f.json"
$env:TTS_LANGUAGE_CODE = "vi-VN"
$env:TTS_VOICE_NAME = "vi-VN-Standard-A"
$env:AUDIO_OUTPUT_DIR = "tts_outputs"

Write-Host "Environment variables set for this PowerShell session."