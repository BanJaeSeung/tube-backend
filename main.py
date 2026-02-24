from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
import requests
import xml.etree.ElementTree as ET
import os
import re
import json
import traceback
import urllib.parse

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

# 🚨 [진짜 최종 완결판] 대용량 웹 프록시(CORS Proxy) 기반 스텔스 엔진
def fetch_transcript_stealth(video_id: str):
    target_url = f"https://www.youtube.com/watch?v={video_id}"
    encoded_url = urllib.parse.quote(target_url)

    # 1. Render IP 차단을 무력화하기 위해 초대형 무료 퍼블릭 프록시들을 거쳐 유튜브를 찌릅니다.
    proxy_urls = [
        target_url, # 혹시 차단이 풀렸을 경우를 대비한 다이렉트 요청
        f"https://api.allorigins.win/raw?url={encoded_url}",
        f"https://api.codetabs.com/v1/proxy?quest={encoded_url}",
        f"https://corsproxy.io/?{encoded_url}"
    ]

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9,ko;q=0.8',
        'Cookie': 'CONSENT=YES+cb.20210328-17-p0.en+FX+478'
    }

    html = None
    for p_url in proxy_urls:
        try:
            print(f"🌐 프록시 스텔스 접속 시도 중: {p_url[:50]}...")
            res = requests.get(p_url, headers=headers, timeout=10)
            if res.status_code == 200 and 'ytInitialPlayerResponse' in res.text:
                html = res.text
                print("✅ 유튜브 원본 HTML 데이터 프록시 획득 성공!")
                break
        except Exception as e:
            print(f"⚠️ 프록시 접속 실패: {e}")
            continue

    if not html:
        raise Exception("유튜브 방화벽이 너무 강력하여 모든 글로벌 프록시망이 차단되었습니다.")

    # 2. HTML 내부에 숨겨진 자막 데이터와 영상 제목(Title) 추출
    caption_tracks = []
    video_title = "알 수 없는 영상" # 제목 스니핑 변수
    
    match = re.search(r'ytInitialPlayerResponse\s*=\s*({.+?})\s*;\s*(?:var\s+meta|<\/script|\n)', html)
    if match:
        try:
            player_response = json.loads(match.group(1))
            # 영상 제목을 성공적으로 가져오면 증명 완료!
            video_title = player_response.get('videoDetails', {}).get('title', video_title)
            caption_tracks = player_response.get('captions', {}).get('playerCaptionsTracklistRenderer', {}).get('captionTracks', [])
        except: pass

    if not caption_tracks:
        track_match = re.search(r'"captionTracks":(\[.*?\])', html)
        if track_match:
            try:
                caption_tracks = json.loads(track_match.group(1))
            except: pass

    # 🚨 자막이 없을 때, "내가 영상 제목까지 다 읽어왔는데 자막만 없는 거야!" 라고 사용자에게 증명
    if not caption_tracks:
        raise Exception(f"[{video_title}] 영상에는 생성된 자막(CC)이 물리적으로 존재하지 않습니다. 자막 기능이 있는 다른 영상으로 시도해주세요.")

    # 3. 최우선 순위: 영어(en) -> 한국어(ko) -> 첫 번째 자막
    target_track = next((track for track in caption_tracks if track.get('languageCode') == 'en'), None)
    if not target_track:
        target_track = next((track for track in caption_tracks if track.get('languageCode') == 'ko'), None)
    if not target_track:
        target_track = caption_tracks[0]

    xml_url = target_track['baseUrl']

    # 4. 자막 원본 파일 다운로드 (이 부분도 프록시 태우기)
    encoded_xml_url = urllib.parse.quote(xml_url)
    xml_proxy_urls = [
        xml_url,
        f"https://api.allorigins.win/raw?url={encoded_xml_url}",
        f"https://api.codetabs.com/v1/proxy?quest={encoded_xml_url}"
    ]

    raw_text = None
    for px_url in xml_proxy_urls:
        try:
            print("🌐 자막 원본 파일 다운로드 중...")
            px_res = requests.get(px_url, headers=headers, timeout=10)
            if px_res.status_code == 200 and len(px_res.text) > 10:
                raw_text = px_res.text
                print("✅ 자막 파일 획득 완료!")
                break
        except: pass

    if not raw_text:
        raise Exception("자막 파일 다운로드 중 서버 연결이 거부되었습니다.")

    # 5. 포맷 파싱 (XML 또는 JSON3 자동 인식)
    data = []
    raw_text = raw_text.strip()
    try:
        # JSON 포맷일 경우
        if raw_text.startswith('{'):
            json_data = json.loads(raw_text)
            for event in json_data.get('events', []):
                if 'segs' in event:
                    text_content = "".join([seg.get('utf8', '') for seg in event['segs']]).replace('\n', ' ').strip()
                    if text_content:
                        data.append({'start': event.get('tStartMs', 0) / 1000.0, 'text': text_content})
        # XML 포맷일 경우
        else:
            root = ET.fromstring(raw_text)
            for child in root:
                if child.tag == 'text':
                    start = float(child.attrib.get('start', 0))
                    text_content = child.text
                    if text_content:
                        text_content = text_content.replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"')
                        data.append({'start': start, 'text': text_content})
    except Exception as e:
        raise Exception(f"자막 변환 실패: {e}")

    if not data:
        raise Exception("추출된 텍스트가 없습니다.")
        
    return data

@app.get("/")
def health_check():
    return {"status": "ok", "message": "초대형 CORS Proxy 스텔스 엔진 실행 중!"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 프록시 기반 스텔스 자막 추출
    try:
        data = fetch_transcript_stealth(video_id)
        full_text = " ".join([t['text'] for t in data])
        print(f"✅ 최종 자막 확보 성공! 전체 길이: {len(full_text)}")
    except Exception as e:
        print(f"❌ 자막 추출 에러: {e}")
        raise HTTPException(status_code=400, detail=f"자막 추출 실패: {str(e)}")

    # 2. AI 분석 (정확히 '한 문장씩' 1:1 매칭 번역)
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
