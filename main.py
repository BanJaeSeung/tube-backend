from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
import yt_dlp
import requests
import os
import re
import json
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

# 🚨 [최종 필살기] yt-dlp 기반 산업 표준 유튜브 추출 엔진
# 데이터센터 IP 차단을 우회하기 위해 내부 프로토콜을 모방합니다.
def fetch_transcript_ytdlp(video_url: str):
    print(f"yt-dlp 엔진 가동 중... 대상: {video_url}")
    
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'extract_flat': False,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=False)
        except Exception as e:
            raise Exception(f"yt-dlp 영상 정보 추출 실패: {str(e)}")

        subs = info.get('subtitles', {})
        auto_subs = info.get('automatic_captions', {})

        target_url = None

        # 1. 수동/자동 영어 자막(json3 포맷) 탐색
        if 'en' in subs:
            target_url = next((fmt['url'] for fmt in subs['en'] if fmt['ext'] == 'json3'), None)
        if not target_url and 'en' in auto_subs:
            target_url = next((fmt['url'] for fmt in auto_subs['en'] if fmt['ext'] == 'json3'), None)
            
        # 2. 수동/자동 한국어 자막 탐색
        if not target_url and 'ko' in subs:
            target_url = next((fmt['url'] for fmt in subs['ko'] if fmt['ext'] == 'json3'), None)
        if not target_url and 'ko' in auto_subs:
            target_url = next((fmt['url'] for fmt in auto_subs['ko'] if fmt['ext'] == 'json3'), None)

        # 3. 영/한이 없으면 아무 언어나 첫 번째 자막 추출
        if not target_url:
            if subs:
                first_lang = list(subs.keys())[0]
                target_url = next((fmt['url'] for fmt in subs[first_lang] if fmt['ext'] == 'json3'), None)
            elif auto_subs:
                first_lang = list(auto_subs.keys())[0]
                target_url = next((fmt['url'] for fmt in auto_subs[first_lang] if fmt['ext'] == 'json3'), None)

        if not target_url:
            raise Exception("이 영상에는 어떠한 자막 데이터도 존재하지 않습니다.")

        # 자막 URL 다운로드 및 파싱
        print("자막 URL 확보 성공. 다운로드 중...")
        res = requests.get(target_url)
        if res.status_code != 200:
            raise Exception("자막 파일 다운로드 중 서버 연결이 거부되었습니다.")

        json3_data = res.json()
        data = []
        
        # JSON3 포맷에서 텍스트와 시간만 정밀하게 파싱
        for event in json3_data.get('events', []):
            if 'segs' in event:
                text = "".join([seg.get('utf8', '') for seg in event['segs']]).replace('\n', ' ').strip()
                if text:
                    data.append({
                        'start': event.get('tStartMs', 0) / 1000.0,
                        'text': text
                    })

        if not data:
            raise Exception("파싱된 자막 텍스트가 비어있습니다.")
            
        return data

@app.get("/")
def health_check():
    return {"status": "ok", "message": "yt-dlp 기반 최강의 우회 서버가 실행 중입니다."}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    if "youtube.com" not in video_url and "youtu.be" not in video_url:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 자막 추출 (yt-dlp 적용)
    try:
        data = fetch_transcript_ytdlp(video_url)
        full_text = " ".join([t['text'] for t in data])
        print(f"✅ yt-dlp 자막 추출 완벽 성공! 전체 길이: {len(full_text)}")
    except Exception as e:
        print(f"❌ 자막 추출 에러: {e}")
        raise HTTPException(status_code=400, detail=f"자막 추출 실패: {str(e)}")

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
