from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

# 🚨 [최종 필살기] 고장난 라이브러리나 막힌 외부 API를 전혀 쓰지 않고, 
# 유튜브 원본 HTML에서 자막 데이터를 직접 뜯어오는 독자적인 크롤링 엔진
def fetch_transcript_direct(video_id):
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9,ko;q=0.8'
        }
        
        # 1. 유튜브 영상 페이지 HTML 가져오기
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text

        # 2. HTML 내부에 숨겨진 자막 JSON 데이터(ytInitialPlayerResponse) 정규식으로 찾기
        match = re.search(r'ytInitialPlayerResponse\s*=\s*({.+?})\s*;\s*(?:var\s+meta|<\/script|\n)', html)
        if not match:
            raise Exception("유튜브 HTML에서 자막 데이터를 찾을 수 없습니다.")

        player_response = json.loads(match.group(1))
        
        # 3. 자막 트랙 리스트 추출
        caption_tracks = player_response.get('captions', {}).get('playerCaptionsTracklistRenderer', {}).get('captionTracks', [])
        
        if not caption_tracks:
            raise Exception("이 영상에는 생성된 자막이 없습니다.")

        # 4. 최우선 순위: 영어(en) -> 한국어(ko) -> 첫 번째 자막
        target_track = next((track for track in caption_tracks if track['languageCode'] == 'en'), None)
        if not target_track:
            target_track = next((track for track in caption_tracks if track['languageCode'] == 'ko'), None)
        if not target_track:
            target_track = caption_tracks[0]

        # 5. 자막 원본 XML 다운로드 및 파싱
        xml_url = target_track['baseUrl']
        xml_response = requests.get(xml_url, timeout=10)
        root = ET.fromstring(xml_response.text)

        data = []
        for child in root:
            if child.tag == 'text':
                start = float(child.attrib.get('start', 0))
                text_content = child.text
                if text_content:
                    # HTML 특수문자 디코딩
                    text_content = text_content.replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"')
                    data.append({'start': start, 'text': text_content})

        if not data:
            raise Exception("추출된 텍스트가 없습니다.")
        
        return data

    except Exception as e:
        raise Exception(f"직접 추출 엔진 실패: {str(e)}")

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Zero-Dependency 독자 추출 엔진이 탑재된 서버입니다."}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 자막 직접 추출 (라이브러리 버림)
    try:
        print(f"독자 엔진으로 유튜브 직접 추출 시도: {video_id}")
        data = fetch_transcript_direct(video_id)
        full_text = " ".join([t['text'] for t in data])
        print(f"✅ 자막 직접 추출 완벽 성공! 전체 길이: {len(full_text)}")
    except Exception as e:
        print(f"❌ 자막 추출 에러: {e}")
        raise HTTPException(status_code=400, detail=f"자막 추출 실패: 비공개 영상이거나 자막이 막혀있습니다. 상세오류: {e}")

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
