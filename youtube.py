import requests
from youtube_transcript_api import YouTubeTranscriptApi
import time

# 유튜브 쇼츠인지 아닌지 구분
def is_youtubeshorts(video_id):
    url = 'https://www.youtube.com/shorts/' + video_id
    req = requests.head(url)
    
    return req.status_code == 200


# YouTube Transcript API로 스크립트 요약
def youtube_transcript(video_id, max_retries=3, retry_delay=1.5):
    """
    YouTube 동영상의 스크립트를 추출하는 함수.
    max_retries: 최대 재시도 횟수
    retry_delay: 재시도 사이의 대기 시간(초)
    """
    for retry in range(max_retries):
        try:
            if retry > 0:
                print(f"자막 추출 재시도 중... ({retry}/{max_retries-1})")
                time.sleep(retry_delay)  # 재시도 사이에 대기 시간 추가
            
            # 사용 가능한 자막 목록 확인
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            
            # 디버깅을 위해 사용 가능한 모든 자막 출력
            print(f"\n영상 ID {video_id}의 사용 가능한 자막 목록:")
            for transcript in transcript_list:
                print(f"- {transcript.language} ({transcript.language_code}): {'자동 생성' if transcript.is_generated else '수동 생성'}")
            
            transcript = None
            
            # 순차적으로 자막 시도
            try:
                # 1. 수동 한국어 자막
                transcript = transcript_list.find_manually_created_transcript(['ko'])
                print('수동 생성 한국어 자막을 불러왔습니다.')
            except Exception as e1:
                try:
                    # 2. 자동 생성 한국어 자막
                    transcript = transcript_list.find_generated_transcript(['ko'])
                    print('자동 생성 한국어 자막을 불러왔습니다.')
                except Exception as e2:
                    try:
                        # 3. 다른 형식의 한국어 자막
                        for t in transcript_list:
                            if t.language_code.startswith('ko'):
                                transcript = t
                                print(f'한국어 자막을 찾았습니다: {t.language_code} ({"자동 생성" if t.is_generated else "수동 생성"})')
                                break
                    except Exception as e3:
                        print('한국어 자막을 찾을 수 없습니다.')
                        # 계속 진행하여 다른 시도를 해보기 위해 continue 하지 않음
            
            if transcript:
                try:
                    # 자막 텍스트 추출
                    transcript_data = transcript.fetch()
                    first_minute = []
                    current_time = 0
                    
                    for line in transcript_data:
                        if current_time > 180:  # 180초 = 3분
                            break
                        first_minute.append(line['text'])
                        current_time += line['duration']
                    
                    result = ' '.join(first_minute)
                    if result:
                        print(f'성공적으로 {current_time:.1f}초 분량의 자막을 추출했습니다.')
                        return result
                    else:
                        print('자막은 찾았으나 내용이 비어있습니다.')
                except Exception as e:
                    print(f'자막 추출 중 오류 발생: {str(e)}')
            else:
                print('사용 가능한 자막을 찾지 못했습니다.')
                
        except Exception as e:
            print(f'스크립트를 불러오는 중 오류가 발생했습니다: {str(e)}')
        
        # 재시도가 남아 있으면 계속 시도
        if retry < max_retries - 1:
            continue
    
    # 모든 시도가 실패하면 명확한 메시지 반환
    return "⚠️ 자막을 가져올 수 없습니다 (여러 번 시도했으나 실패)"
