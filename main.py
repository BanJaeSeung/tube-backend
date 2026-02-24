from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
import os
import re
import json

app = FastAPI()

# 프론트엔드(Vercel)와의 통신을 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini AI 설정
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("WARNING: GEMINI_API_KEY is not set in environment variables.")

# 가장 안정적인 gemini-1.5-flash 모델 사용
model = genai.GenerativeModel('gemini-1.5-flash')

def extract_video_id(url: str):
    """유튜브 URL에서 비디오 ID를 추출하는 함수"""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

@app.get("/")
def health_check():
    """서버 상태 확인용 엔드포인트"""
    return {"status": "ok", "message": "Server is running"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    print(f"--- Starting analysis for: {video_url} ---")
    video_id = extract_video_id(video_url)
    
    if not video_id:
        print("Error: Invalid YouTube URL")
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    try:
        # 1. 유튜브 자막 추출 단계
        print(f"Attempting to fetch transcript for video: {video_id}")
        
        # 라이브러리 버전 확인용 로그
        if not hasattr(YouTubeTranscriptApi, 'list_transcripts'):
            print("CRITICAL: YouTubeTranscriptApi version is too old.")
            raise Exception("Library version issue. Update requirements.txt to youtube-transcript-api>=0.6.2")

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        try:
            # 수동 자막(영어) 우선 시도
            transcript = transcript_list.find_manually_created_transcript(['en'])
            print("Found manual English transcript.")
        except:
            try:
                # 자동 생성 자막(영어) 시도
                transcript = transcript_list.find_generated_transcript(['en'])
                print("Found auto-generated English transcript.")
            except Exception as e:
                print(f"No English transcript found, attempting translation: {str(e)}")
                # 다른 언어 자막을 영어로 번역해서 가져오기
                transcript = transcript_list.find_transcript(['en']).translate('en')
                print("Translated other language transcript to English.")

        data = transcript.fetch()
        full_text = " ".join([t['text'] for t in data])
        print(f"Successfully fetched transcript. Length: {len(full_text)} characters.")

        # 2. AI 분석 단계 (Gemini)
        print("Sending prompt to Gemini AI...")
        prompt = f"""
        당신은 전문 번역가이자 언어 학습 가이드입니다.
        제공된 유튜브 영어 스크립트를 문맥에 따라 3~5문장씩 의미 단위로 나누어 번역해주세요.
        또한 학습하기 좋은 주요 단어 5개를 선정해주세요.
        
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
        response_text = response.text
        print("Received response from Gemini AI.")
        
        # AI 응답에서 JSON 데이터만 추출
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            
        ai_result = json.loads(response_text)
        
        # 3. 타임라인(시작 시간) 매칭
        chunk_size = max(1, len(data) // len(ai_result['script']))
        for i, item in enumerate(ai_result['script']):
            idx = min(i * chunk_size, len(data) - 1)
            item['start'] = data[idx]['start']
            item['id'] = i + 1
            item['speaker'] = "Speaker"

        print("Analysis complete. Sending data to frontend.")
        return ai_result

    except Exception as e:
        error_msg = str(e)
        print(f"CRITICAL ERROR: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {error_msg}")
