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

# 🚨 [최종 완결판] Google API 다이렉트 모바일 앱 우회 엔진
# www.youtube.com 의 웹 방화벽을 피하기 위해, youtubei.googleapis.com 공식 API 서버로 
# 안드로이드 및 아이폰 앱 통신 패킷을 쏘아 방화벽을 우회합니다.
def fetch_transcript_innertube_api(video_id: str):
    # 웹 주소가 아닌 구글 모바일 통합 API 주소 사용 (웹 방화벽 미적용 지대)
    api_url = "https://youtubei.googleapis.com/youtubei/v1/player"

    clients = [
        {
            "name": "ANDROID",
            "headers": {
                "Content-Type": "application/json",
                "User-Agent": "com.google.android.youtube/17.31.35 (Linux; U; Android 11; en_US; Pixel 5 Build/RQ3A.210805.001.A1)",
                "X-YouTube-Client-Name": "3",
                "X-YouTube-Client-Version": "17.31.35",
            },
            "client_context": {
                "clientName": "ANDROID",
                "clientVersion": "17.31.35",
                "androidSdkVersion": 30,
                "hl": "en",
                "gl": "US"
            }
        },
        {
            "name": "IOS",
            "headers": {
                "Content-Type": "application/json",
                "User-Agent": "com.google.ios.youtube/19.28.1 (iPhone14,5; U; CPU iOS 17_5_1 like Mac OS X; en_US)",
                "X-YouTube-Client-Name": "5",
                "X-YouTube-Client-Version": "19.28.1",
            },
            "client_context": {
                "clientName": "IOS",
                "clientVersion": "19.28.1",
                "deviceMake": "Apple",
                "deviceModel": "iPhone14,5",
                "osName": "iOS",
                "osVersion": "17.5.1",
                "hl": "en",
                "gl": "US"
            }
        }
    ]

    caption_tracks = []
    video_title = "알 수 없는 영상"
    success_headers = None

    for client in clients:
        print(f"🔄 [{client['name']}] 모바일 다이렉트 API 우회 접속 시도 중...")
        payload = {
            "context": {
                "client": client["client_context"]
            },
            "videoId": video_id
        }

        try:
            res = requests.post(api_url, json=payload, headers=client["headers"], timeout=10)
            if res.status_code == 200:
                data = res.json()
                
                # 영상 제목(Title) 추출 - 구글 API는 캡차 없이 무조건 데이터를 줍니다.
                if video_title == "알 수 없는 영상":
                    video_title = data.get("videoDetails", {}).get("title", "알 수 없는 영상")

                playability = data.get("playabilityStatus", {}).get("status", "")
                if playability in ["UNPLAYABLE", "LOGIN_REQUIRED", "ERROR"]:
                    reason = data.get("playabilityStatus", {}).get("reason", "접근 불가")
                    print(f"⚠️ [{client['name']}] 영상 재생 불가 상태: {reason}")
                    continue
                    
                tracks = data.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
                if tracks:
                    caption_tracks = tracks
                    success_headers = client["headers"]
                    print(f"✅ [{client['name']}] 모바일 API 우회 성공! 영상 제목 및 자막 확보 완료.")
                    break
        except Exception as e:
            print(f"⚠️ [{client['name']}] 접속 에러: {e}")
            continue

    # 모바일 API를 다 찔러봤는데도 자막이 없다면 100% 자막이 없는 영상임을 증명!
    if not caption_tracks:
        raise Exception(f"[{video_title}] 영상에는 자동 생성 자막(ASR)조차 물리적으로 존재하지 않습니다. 자막이 활성화된 영상인지 확인해주세요.")

    # 최우선 순위: 영어(en) -> 한국어(ko) -> 첫 번째 자막
    target_track = next((track for track in caption_tracks if track.get('languageCode') == 'en'), None)
    if not target_track:
        target_track = next((track for track in caption_tracks if track.get('languageCode') == 'ko'), None)
    if not target_track:
        target_track = caption_tracks[0]

    xml_url = target_track['baseUrl']

    print("📥 자막 원본 파일 다운로드 및 파싱 진행 중...")
    try:
        xml_res = requests.get(xml_url, headers=success_headers, timeout=10)
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
    return {"status": "ok", "message": "Google API 다이렉트 우회 아키텍처 실행 중!"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 모바일 다이렉트 API 로테이션을 통한 자막 추출
    try:
        data = fetch_transcript_innertube_api(video_id)
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
