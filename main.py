from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
import os
import re
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash-exp')

def extract_video_id(url: str):
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

@app.get("/api/analyze")
def analyze_youtube_video(video_url: str):
    video_id = extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    try:
        # 개선된 자막 추출 로직: 수동 자막과 자동 생성 자막 모두 검색
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        try:
            # 1. 먼저 수동으로 작성된 영어 자막을 찾습니다.
            transcript = transcript_list.find_manually_created_transcript(['en'])
        except:
            try:
                # 2. 없다면 자동 생성된 영어 자막을 찾습니다.
                transcript = transcript_list.find_generated_transcript(['en'])
            except:
                # 3. 그것도 없다면 다른 언어를 영어로 번역한 자막이라도 가져옵니다.
                transcript = transcript_list.find_transcript(['en']).translate('en')

        data = transcript.fetch()
        full_text = " ".join([t['text'] for t in data])
        
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
        
        스크립트: {full_text[:6000]}
        """
        
        response = model.generate_content(prompt)
        response_text = response.text
        
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            
        ai_result = json.loads(response_text)
        
        # 타임스탬프 매칭
        chunk_size = max(1, len(data) // len(ai_result['script']))
        for i, item in enumerate(ai_result['script']):
            idx = min(i * chunk_size, len(data) - 1)
            item['start'] = data[idx]['start']
            item['id'] = i + 1
            item['speaker'] = "Speaker"

        return ai_result

    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"자막을 찾을 수 없거나 분석에 실패했습니다: {str(e)}")
