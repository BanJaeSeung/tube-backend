from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
import requests
import xml.etree.ElementTree as ET
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

def extract_video_id(url: str):
    """유튜브 URL에서 11자리 고유 영상 ID를 추출"""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

# 🚨 [최종 아키텍처] 다중 플랫폼(Multi-Platform) 로테이션 위장 엔진
# youtube-transcript.io 와 동일한 원리로, CC가 없는 영상의 '자동 생성 자막'을 
# 뽑아내기 위해 웹, 안드로이드, iOS, 스마트TV 등 4가지 신분으로 바꿔가며 유튜브를 공략합니다.
def fetch_transcript_innertube_multi(video_id: str):
    api_url = "https://www.youtube.com/youtubei/v1/player"

    # 1. 유튜브 서버가 각 기기마다 내려주는 자막 데이터가 다르기 때문에 4가지 신분증 준비
    clients = [
        # 1순위: 가장 기본적이고 자동 자막을 잘 주는 WEB
        {"clientName": "WEB", "clientVersion": "2.20240105.01.00"},
        # 2순위: 스마트TV (연령 제한이나 까다로운 방화벽을 잘 무시함)
        {"clientName": "TVHTML5", "clientVersion": "7.20230405.08.01"},
        # 3순위: 안드로이드 공식 앱
        {"clientName": "ANDROID", "clientVersion": "17.31.35"},
        # 4순위: 아이폰 공식 앱
        {"clientName": "IOS", "clientVersion": "19.28.1", "deviceMake": "Apple", "deviceModel": "iPhone14,5", "osName": "iOS", "osVersion": "17.5.1"}
    ]

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"
    }

    caption_tracks = []
    video_title = "알 수 없는 영상"

    # 2. 4가지 신분으로 차례대로 유튜브 내부망(InnerTube) 공격
    for client in clients:
        print(f"🔄 [{client['clientName']}] 플랫폼으로 위장 접속 시도 중...")
        payload = {
            "context": {
                "client": {
                    **client,
                    "hl": "en",
                    "gl": "US"
                }
            },
            "videoId": video_id
        }

        try:
            res = requests.post(api_url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                
                # 영상 제목은 최초 성공 시 무조건 스니핑
                if video_title == "알 수 없는 영상":
                    video_title = data.get("videoDetails", {}).get("title", "알 수 없는 영상")
                    
                # 자막 트랙 확인
                tracks = data.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
                if tracks:
                    caption_tracks = tracks
                    print(f"✅ [{client['clientName']}] 플랫폼 위장 성공! 자동/수동 자막 데이터 확보 완료.")
                    break
        except Exception as e:
            print(f"⚠️ [{client['clientName']}] 접속 에러: {e}")
            continue

    # 4가지 기기로 다 찔러봤는데도 없으면 정말 물리적으로 자막이 없는 영상임
    if not caption_tracks:
        raise Exception(f"[{video_title}] 영상에는 자동 생성 자막(ASR)조차 존재하지 않습니다. 자막이 1초라도 포함된 영상인지 확인해주세요.")

    # 3. 최우선 순위: 영어(en) -> 한국어(ko) -> 첫 번째 자막
    target_track = next((track for track in caption_tracks if track.get('languageCode') == 'en'), None)
    if not target_track:
        target_track = next((track for track in caption_tracks if track.get('languageCode') == 'ko'), None)
    if not target_track:
        target_track = caption_tracks[0]

    xml_url = target_track['baseUrl']

    # 4. 자막 원본 파일 다운로드 및 파싱
    print("📥 자막 파일 다운로드 및 파싱 진행 중...")
    try:
        xml_res = requests.get(xml_url, headers=headers, timeout=10)
        parsed_data = []
        root = ET.fromstring(xml_res.text)
        
        for child in root:
            if child.tag == 'text':
                start = float(child.attrib.get('start', 0))
                text_content = child.text
                if text_content:
                    text_content = text_content.replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"')
                    parsed_data.append({'start': start, 'text': text_content})
                    
        if not parsed_data:
            raise Exception("파싱된 텍스트가 비어있습니다.")
            
        return parsed_data
        
    except Exception as e:
        raise Exception(f"자막 데이터 변환 실패: {e}")

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Multi-Platform 로테이션 아키텍처 실행 중!"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 다중 플랫폼 로테이션을 통한 자막 추출
    try:
        data = fetch_transcript_innertube_multi(video_id)
        full_text = " ".join([t['text'] for t in data])
        print(f"✅ 최종 자막 확보 성공! 전체 길이: {len(full_text)}")
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

        print("✅ AI 번역 및 데이터 처리 성공!")
        return ai_result

    except Exception as e:
        print(f"❌ AI 분석 에러:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI 번역 실패: {str(e)}")
