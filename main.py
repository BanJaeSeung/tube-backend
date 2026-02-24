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

# 🚨 [만능 자막 파서] XML, JSON3, WebVTT 등 각 노드마다 다르게 주는 포맷을 완벽하게 파싱
def parse_universal_subtitles(sub_text: str):
    parsed_data = []
    sub_text = sub_text.strip()
    
    # 1. XML 파싱 (유튜브 기본 포맷)
    if sub_text.startswith('<?xml') or sub_text.startswith('<transcript'):
        try:
            root = ET.fromstring(sub_text)
            for child in root:
                if child.tag == 'text':
                    start = float(child.attrib.get('start', 0))
                    text_content = child.text
                    if text_content:
                        text_content = text_content.replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"')
                        parsed_data.append({'start': start, 'text': text_content})
            if parsed_data: return parsed_data
        except: pass

    # 2. JSON3 파싱 (Piped 망 제공 포맷)
    if sub_text.startswith('{'):
        try:
            json_data = json.loads(sub_text)
            for event in json_data.get('events', []):
                if 'segs' in event:
                    text_content = "".join([seg.get('utf8', '') for seg in event['segs']]).replace('\n', ' ').strip()
                    if text_content:
                        parsed_data.append({
                            'start': event.get('tStartMs', 0) / 1000.0,
                            'text': text_content
                        })
            if parsed_data: return parsed_data
        except: pass

    # 3. WebVTT 파싱 (Invidious 망 제공 포맷)
    if "WEBVTT" in sub_text:
        try:
            blocks = sub_text.split('\n\n')
            for block in blocks:
                lines = block.strip().split('\n')
                time_line = None
                text_lines = []
                
                for i, line in enumerate(lines):
                    if '-->' in line:
                        time_line = line
                        text_lines = lines[i+1:]
                        break
                        
                if time_line:
                    time_str = time_line.split('-->')[0].strip()
                    parts = time_str.split(':')
                    try:
                        if len(parts) == 3: # 00:00:05.000 형식
                            start = float(parts[0])*3600 + float(parts[1])*60 + float(parts[2].replace(',', '.'))
                        elif len(parts) == 2: # 00:05.000 형식
                            start = float(parts[0])*60 + float(parts[1].replace(',', '.'))
                        else:
                            start = 0.0
                            
                        text = " ".join(text_lines).strip()
                        text = re.sub(r'<[^>]+>', '', text) # HTML/VTT 태그 제거
                        if text:
                            parsed_data.append({'start': start, 'text': text})
                    except:
                        pass
            if parsed_data: return parsed_data
        except: pass
            
    return parsed_data

# 🚨 [최종 완결판] Invidious + Piped 하이브리드 분산 노드 우회 엔진
def fetch_transcript_decentralized(video_id: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    # 1. Invidious 노드 풀 (안정성 최상, 최우선 시도)
    invidious_nodes = [
        "https://invidious.fdn.fr",
        "https://yt.artemislena.eu",
        "https://invidious.perennialte.ch",
        "https://invidious.flokinet.to",
        "https://inv.tux.pizza",
        "https://invidious.lunar.icu",
        "https://invidious.projectsegfau.lt"
    ]
    
    for node in invidious_nodes:
        try:
            print(f"🌐 Invidious 망 우회 시도 중: {node}")
            res = requests.get(f"{node}/api/v1/videos/{video_id}", headers=headers, timeout=6)
            if res.status_code != 200: continue
            
            captions = res.json().get('captions', [])
            if not captions: continue
            
            target = next((c for c in captions if c.get('languageCode') == 'en'), None)
            if not target: target = next((c for c in captions if c.get('languageCode') == 'ko'), None)
            if not target: target = captions[0]
            
            cap_url = node + target.get('url')
            cap_res = requests.get(cap_url, headers=headers, timeout=6)
            if cap_res.status_code == 200:
                parsed = parse_universal_subtitles(cap_res.text)
                if parsed: 
                    print(f"✅ Invidious 노드({node})에서 자막 탈취 성공!")
                    return parsed
        except Exception as e:
            print(f"⚠️ 노드 연결 실패 ({node}): {e}")
            continue

    # 2. Piped 노드 풀 (Invidious망 전멸 시 페일오버 작동)
    piped_nodes = [
        "https://api.piped.privacydev.net",
        "https://pipedapi.tokhmi.xyz",
        "https://pipedapi.syncpundit.io",
        "https://pipedapi.smnz.de",
        "https://piped-api.garudalinux.org",
        "https://pipedapi.drgns.space"
    ]

    for node in piped_nodes:
        try:
            print(f"🌐 Piped 망 페일오버(Failover) 시도 중: {node}")
            res = requests.get(f"{node}/streams/{video_id}", headers=headers, timeout=6)
            if res.status_code != 200: continue
            
            subtitles = res.json().get('subtitles', [])
            if not subtitles: continue
            
            target = next((s for s in subtitles if s.get('code') == 'en' and not s.get('autoGenerated')), None)
            if not target: target = next((s for s in subtitles if s.get('code') == 'en'), None)
            if not target: target = next((s for s in subtitles if s.get('code') == 'ko'), None)
            if not target: target = subtitles[0]
                
            sub_url = target.get('url')
            sub_res = requests.get(sub_url, headers=headers, timeout=6)
            if sub_res.status_code == 200:
                parsed = parse_universal_subtitles(sub_res.text)
                if parsed: 
                    print(f"✅ Piped 노드({node})에서 자막 탈취 성공!")
                    return parsed
        except Exception as e:
            print(f"⚠️ 노드 연결 실패 ({node}): {e}")
            continue
            
    raise Exception("모든 13개 글로벌 하이브리드 노드가 응답하지 않거나 차단되었습니다. 잠시 후 다시 시도해주세요.")

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Invidious+Piped 다중화 메쉬망(Hybrid Mesh) 엔진 실행 중!"}

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효하지 않은 유튜브 URL입니다.")

    # 1. 다중 분산 네트워크를 통한 자막 우회 추출
    try:
        data = fetch_transcript_decentralized(video_id)
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
