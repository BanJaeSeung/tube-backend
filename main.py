from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
import os
import re
import json
import requests
import xml.etree.ElementTree as ET
import traceback

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

# 🚨 [핵심] Render IP 차단을 뚫기 위한 '서드파티 우회(Bypass) API' 함수
def fetch_transcript_bypass(video_id):
    try:
        # 유튜브를 직접 찌르지 않고, 외부 전용 서버를 우회하여 자막을 가로챕니다.
        url = f"https://youtubetranscript.com/?server_vid2={video_id}"
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            raise Exception("우회 서버 연결 실패")
        
        root = ET.fromstring(response.content)
        if root.tag == 'error':
            raise Exception(f"자막 없음: {root.text}")
            
        data = []
        for child in root:
            if child.tag == 'text':
                start = float(child.attrib.get('start', 0))
                # HTML 특수문자 디코딩 처리
                text = child.text.replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"')
                data.append({'start': start, 'text': text})
        
        if not data:
            raise Exception("추출된 텍스트가 없습니다.")
        return data
    except Exception as e:
        raise Exception(f"우회 추출 최종 실패: {str(e)}")

@app.get("/")
def health_check():
    return {"status": "ok", "message": "강력한 우회(Proxy) 추출 기능이 탑재된 서버입니다."}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 자막 추출 (이중화 시스템)
    data = None
    try:
        print("1차 시도: 기본 라이브러리로 추출 시도...")
        data = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'ko'])
    except Exception as e1:
        print(f"1차 시도 실패 (유튜브 IP 차단됨). 2차 우회 시도 시작...: {e1}")
        try:
            print("2차 시도: 외부 프록시(Bypass) API를 통한 강제 추출...")
            data = fetch_transcript_bypass(video_id)
        except Exception as e2:
            print(f"2차 시도까지 실패: {e2}")
            raise HTTPException(status_code=400, detail="자막 추출 실패: 해당 영상에 자막이 완전히 막혀있거나 존재하지 않습니다.")

    if not data:
        raise HTTPException(status_code=400, detail="자막 데이터를 찾을 수 없습니다.")

    full_text = " ".join([t['text'] for t in data])
    print(f"✅ 자막 추출 완벽 성공! 전체 길이: {len(full_text)}")

    # 2. AI 분석 (요구사항: 정확히 '한 문장씩' 1:1 매칭 번역)
    try:
        print("Gemini AI로 한 문장씩 번역 요청 중...")
        prompt = f"""
        당신은 최고의 영어 학습 선생님입니다.
        제공된 유튜브 스크립트를 **정확히 한 문장씩(Sentence by sentence)** 나누어서 영어 원문과 한국어 번역을 1:1로 완벽하게 매칭해주세요.
        문장이 아닌 구문이 섞여 있더라도, 학습자가 읽기 편한 하나의 문장 단위로 합치거나 다듬어주세요.
        그리고 전체 내용에서 학습하기 좋은 핵심 영단어 5개를 뽑아주세요.

        반드시 아래 JSON 형식으로만 응답하세요:
        {{
            "script": [
                {{"text": "First English sentence.", "translation": "첫 번째 한국어 번역."}},
                {{"text": "Second English sentence.", "translation": "두 번째 한국어 번역."}}
            ],
            "vocab": [
                {{"word": "단어", "meaning": "뜻"}}
            ]
        }}

        스크립트: {full_text[:8000]}
        """
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        # JSON 파싱 안정화
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

        print("✅ AI 번역 및 데이터 처리 성공!")
        return ai_result

    except Exception as e:
        print(f"❌ AI 분석 에러:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI 번역 실패: {str(e)}")
