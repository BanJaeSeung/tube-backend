from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
import os
import re
import json

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
    """유튜브 URL에서 11자리 고유 영상 ID를 추출"""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

@app.get("/")
def health_check():
    return {"status": "ok", "message": "무적 호환성 서버가 실행 중입니다!"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 자막 추출 (에러 원천 차단 로직)
    try:
        # 에러를 일으키던 최신 기능 대신, 가장 오래되고 안정적인 기본 함수 사용!
        # 영어('en')를 먼저 찾고, 없으면 한국어('ko')를 찾습니다.
        data = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'ko'])
        full_text = " ".join([t['text'] for t in data])
        print(f"자막 추출 성공! 글자 수: {len(full_text)}")
        
    except Exception as e:
        print(f"자막 추출 에러: {e}")
        raise HTTPException(status_code=400, detail=f"자막 추출 실패: 영어/한국어 자막이 없거나 비공개된 영상입니다. 상세: {str(e)}")

    # 2. AI 분석 (Gemini)
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
        
        # JSON 포맷 안전 처리 (백틱 제거)
        if response_text.startswith("```json"):
            response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"):
            response_text = response_text[3:-3].strip()

        ai_result = json.loads(response_text)

        # 3. 타임스탬프 (시작 시간) 매칭
        chunk_size = max(1, len(data) // max(1, len(ai_result.get('script', [1]))))
        for i, item in enumerate(ai_result.get('script', [])):
            idx = min(i * chunk_size, len(data) - 1)
            item['start'] = data[idx]['start']
            item['id'] = i + 1
            item['speaker'] = "Speaker"

        return ai_result

    except Exception as e:
        print(f"AI 분석 에러: {e}")
        raise HTTPException(status_code=500, detail=f"AI 분석 및 데이터 처리 실패: {str(e)}")