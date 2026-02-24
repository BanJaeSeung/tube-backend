import os
import sys
import subprocess
import traceback

# 🚨 [초강수 트러블슈팅] 서버 구동 직전, 꼬여있는 라이브러리를 강제 삭제 및 클린 설치
try:
    print("🚀 [System] 오염된 라이브러리 강제 삭제 및 클린 설치 시작...")
    # 1. 기존 라이브러리 무조건 삭제
    subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "-y", "youtube-transcript-api"])
    # 2. 캐시를 무시하고 0.6.2 버전으로 강제 재설치
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", "youtube-transcript-api==0.6.2"])
    print("✅ [System] 라이브러리 재설치 완벽 성공!")
except Exception as e:
    print(f"⚠️ [System] 재설치 중 예외 발생: {e}")

# 클린 설치 완료 후, 모듈 임포트
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
import re
import json

# 🔍 [디버깅] 도대체 어떤 파일을 읽어오고 있는지 로그에 출력 (범인 색출용)
import youtube_transcript_api
from youtube_transcript_api import YouTubeTranscriptApi

print(f"🔍 [Debug] 현재 로드된 라이브러리 실제 위치: {youtube_transcript_api.__file__}")
print(f"🔍 [Debug] 사용 가능한 기능 목록: {dir(YouTubeTranscriptApi)}")

app = FastAPI()

# Vercel(프론트엔드)과의 통신을 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini API 인증
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel('gemini-1.5-flash')

def extract_video_id(url: str):
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

@app.get("/")
def health_check():
    return {"status": "ok", "message": "강제 자가 치유(Auto-Healing) 서버 구동 중!"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    try:
        # 가장 안정적인 list_transcripts 사용
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        try:
            transcript = transcript_list.find_manually_created_transcript(['en', 'ko'])
        except:
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'ko'])
            except:
                transcript = list(transcript_list)[0]

        if transcript.language_code != 'en':
            transcript = transcript.translate('en')

        data = transcript.fetch()
        full_text = " ".join([t['text'] for t in data])
        print(f"✅ 자막 추출 성공! 길이: {len(full_text)}")
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ 자막 추출 에러: {error_msg}")
        raise HTTPException(status_code=400, detail=f"자막 추출 실패: {error_msg}")

    # AI 분석 파트
    try:
        prompt = f"""
        당신은 전문 번역가이자 언어 학습 가이드입니다.
        제공된 유튜브 영어 스크립트를 문맥에 따라 3~5문장씩 의미 단위로 나누어 한국어로 번역해주세요.
        또한 학습하기 좋은 주요 영어 단어 5개를 선정해주세요.

        반드시 아래 JSON 형식으로만 응답하세요:
        {{
            "script": [
                {{"text": "English sentences...", "translation": "한국어 번역..."}}
            ],
            "vocab": [
                {{"word": "단어", "meaning": "뜻"}}
            ]
        }}

        스크립트: {full_text[:7000]}
        """
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        if response_text.startswith("```json"):
            response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"):
            response_text = response_text[3:-3].strip()

        ai_result = json.loads(response_text)

        chunk_size = max(1, len(data) // max(1, len(ai_result.get('script', [1]))))
        for i, item in enumerate(ai_result.get('script', [])):
            idx = min(i * chunk_size, len(data) - 1)
            item['start'] = data[idx]['start']
            item['id'] = i + 1
            item['speaker'] = "Speaker"

        return ai_result

    except Exception as e:
        print(f"❌ AI 분석 에러: {e}")
        raise HTTPException(status_code=500, detail=f"AI 분석 실패: {str(e)}")