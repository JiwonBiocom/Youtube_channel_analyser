import streamlit as st
from googleapiclient.discovery import build
import pandas as pd
from PIL import Image
import requests
from io import BytesIO
import os
from dotenv import load_dotenv
from openai import OpenAI
import re

from youtube_transcript_api import YouTubeTranscriptApi

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# 앱 설정
st.set_page_config(page_title="유튜브 채널 분석기", layout="wide")

load_dotenv()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)

# 유튜브 쇼츠인지 아닌지 구분
def is_youtubeshorts(video_id):
    url = 'https://www.youtube.com/shorts/' + video_id
    req = requests.head(url)
    
    return req.status_code == 200

# YouTube Transcript API로 스크립트 요약
def youtube_transcript(video_id):
    try:
        # 사용 가능한 자막 목록 확인
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # 디버깅을 위해 사용 가능한 모든 자막 출력
        print(f"\n영상 ID {video_id}의 사용 가능한 자막 목록:")
        for transcript in transcript_list:
            print(f"- {transcript.language} ({transcript.language_code}): {'자동 생성' if transcript.is_generated else '수동 생성'}")
        
        transcript = None
        transcript_data = None
        
        # 순차적으로 자막 시도
        try:
            # 1. 수동 한국어 자막
            transcript = transcript_list.find_manually_created_transcript(['ko'])
            print('수동 생성 한국어 자막을 불러왔습니다.')
        except:
            try:
                # 2. 자동 생성 한국어 자막
                transcript = transcript_list.find_generated_transcript(['ko'])
                print('자동 생성 한국어 자막을 불러왔습니다.')
            except:
                try:
                    # 3. 다른 형식의 한국어 자막
                    for t in transcript_list:
                        if t.language_code.startswith('ko'):
                            transcript = t
                            print(f'한국어 자막을 찾았습니다: {t.language_code} ({"자동 생성" if t.is_generated else "수동 생성"})')
                            break
                except:
                    print('한국어 자막을 찾을 수 없습니다.')
                    return ''
        
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
                    return ''
            except Exception as e:
                print(f'자막 추출 중 오류 발생: {str(e)}')
                return ''
        else:
            print('사용 가능한 자막을 찾지 못했습니다.')
            return ''
            
    except Exception as e:
        print(f'스크립트를 불러오는 중 오류가 발생했습니다: {str(e)}')
        return ''

class YouTubeAnalyzer:
    def __init__(self, api_key):
        self.youtube = build('youtube', 'v3', developerKey=api_key)
    
    # Extract channel ID from URL or custom URL
    def get_channel_id(self, channel_url):
        if 'youtube.com/channel/' in channel_url:
            return channel_url.split('channel/')[1].split('/')[0]
        elif 'youtube.com/@' in channel_url:
            username = channel_url.split('@')[1].split('/')[0]
            request = self.youtube.channels().list(
                part='id',
                forUsername=username
            )
            try:
                response = request.execute()
                if response.get('items'):
                    return response['items'][0]['id']
            except:
                pass
            
            # If the above method fails, try with search
            request = self.youtube.search().list(
                part='snippet',
                q=username,
                type='channel',
                maxResults=1
            )
            response = request.execute()
            
            # Verify the channel handle matches
            for item in response['items']:
                channel_id = item['snippet']['channelId']
                channel_info = self.youtube.channels().list(
                    part='snippet',
                    id=channel_id
                ).execute()
                
                if channel_info['items'][0]['snippet'].get('customUrl', '').lower() == f'@{username.lower()}':
                    return channel_id
            
            raise ValueError(f"Could not find channel ID for {channel_url}")
    
    # Get channel statistics including subscriber count
    def get_channel_stats(self, channel_id):
        request = self.youtube.channels().list(
            part='statistics,snippet',
            id=channel_id
        )
        response = request.execute()
        channel_info = response['items'][0]
        return {
            'title': channel_info['snippet']['title'],
            'subscribers': int(channel_info['statistics']['subscriberCount']),
            'thumbnail': channel_info['snippet']['thumbnails']['high']['url']
        }
    
    # 해당 채널의 동영상 정보
    def get_all_videos(self, channel_id):
        videos = []
        next_page_token = None
        
        while True:
            request = self.youtube.search().list(
                part='snippet',
                channelId=channel_id,
                maxResults=50,
                type='video',
                pageToken=next_page_token,
                order='date'  # 최신 영상부터
            )
            response = request.execute()
            
            # Get video IDs for batch statistics request
            video_ids = [item['id']['videoId'] for item in response['items']]
            
            # Get video statistics in batch
            stats_request = self.youtube.videos().list(
                part='statistics',
                id=','.join(video_ids)
            )
            stats_response = stats_request.execute()
            
            # Combine video information with statistics
            for video, stats in zip(response['items'], stats_response['items']):
                videos.append({
                    'title': video['snippet']['title'],
                    'thumbnail': video['snippet']['thumbnails']['high']['url'],
                    'views': int(stats['statistics'].get('viewCount', 0)),
                    'like_count': int(stats['statistics'].get('likeCount', 0)),
                    'comment_count': int(stats['statistics'].get('commentCount', 0)),
                    'published_at': video['snippet']['publishedAt'],
                    'video_id': video['id']['videoId']
                })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return videos

    # 상위 n개 댓글
    def get_top_comments(self, video_id, max_results=3):
        """Get top comments for a specific video"""
        try:
            request = self.youtube.commentThreads().list(
                part='snippet',
                videoId=video_id,
                maxResults=max_results,
                order='relevance'  # 인기순 정렬
            )
            response = request.execute()
            
            comments = []
            for item in response.get('items', []):
                comment = item['snippet']['topLevelComment']['snippet']
                comments.append({
                    'author': comment['authorDisplayName'],
                    'text': comment['textDisplay'],
                    'like_count': comment['likeCount'],
                    'published_at': comment['publishedAt']
                })
            
            return comments
        except Exception as e:
            # 댓글이 비활성화된 경우 등의 예외 처리
            return [{'author': '댓글 없음', 'text': '댓글을 가져올 수 없습니다 (비활성화되었거나 접근 불가)', 'like_count': 0, 'published_at': ''}]

# 댓글 감정 분석
def analyze_comments_sentiment(client, comments, video_title):
    # 모든 댓글 텍스트 준비
    comment_texts = []
    for comment in comments:
        author = comment['author']
        likes = comment['like_count']
        if comment['text'] and comment['text'] != '댓글을 가져올 수 없습니다 (비활성화되었거나 접근 불가)':
            comment_texts.append(f"작성자: {author}, 내용: {comment['text']}, 좋아요 수: {likes}")
    
    # 댓글이 없는 경우
    if not comment_texts:
        return "댓글이 없거나 분석할 수 없습니다."
    
    all_comments = "\n".join(comment_texts)
    
    # 프롬프트 작성 - 모든 댓글을 함께 분석하도록 요청
    prompt = f"""
    다음은 YouTube 동영상 "{video_title}"에 달린 인기 댓글들입니다:
    {all_comments}
    
    위 모든 댓글들을 종합적으로 분석하여, 이 동영상에 대한 시청자들의 전반적인 감정과 반응을 아래 내용대로 150 단어 이내로 간결하게 요약해주세요.
    
    - 전체적인 감정 톤(긍정적/부정적/중립적)
    - 공통적으로 나타나는 주요 감정이나 의견
    - 시청자들의 전반적인 반응
    """
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # OpenAI API 호출
            response = client.chat.completions.create(
                model="gpt-4o-2024-08-06",
                messages=[
                    {"role": "system", "content": "당신은 유튜브 댓글을 분석하는 전문가입니다. 댓글에서 감정, 의견, 반응을 객관적으로 파악하여 요약해주세요."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=300
            )
            
            # 응답에서 텍스트 추출
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                return f"감정 분석 중 오류 발생: {str(e)}"
    
    return "감정 분석을 수행할 수 없습니다."

# 새로 만들 동영상 제목, 썸네일, 스크립트 추천
def generate_video_content(client, keyword, channel_info, top_videos_data, sentiment_results):
    # 채널 정보 추출
    channel_name = channel_info['title']
    subscriber_count = channel_info['subscribers']
    
    # 인기 동영상 정보 추출
    video_insights = []
    for i, video in enumerate(top_videos_data[:3]):  # 상위 3개 동영상만 사용
        video_insights.append(f"""
        동영상 {i+1}: "{video['title']}"
        - 조회수: {video['views']}
        - 좋아요 수: {video['like_count']}
        - 댓글 수: {video['comment_count']}
        """)
    
    # 감정 분석 결과 추출
    sentiment_insights = []
    for result in sentiment_results[:3]:  # 상위 3개 동영상의 감정 분석 결과만 사용
        sentiment_insights.append(f"""
        동영상 "{result['영상 제목']}"의 댓글 감정 분석:
        {result['감정 분석']}
        """)
    
    # 모든 정보를 하나의 문자열로 결합
    channel_summary = f"채널명: {channel_name}, 구독자 수: {subscriber_count:,}명"
    video_summary = "\n".join(video_insights)
    sentiment_summary = "\n".join(sentiment_insights)
    
    # 프롬프트 작성
    prompt = f"""당신은 유튜브 콘텐츠 제작 전문가입니다. 아래 정보를 바탕으로 "{keyword}" 주제의 유튜브 동영상을 위한 제목 3개와 각각에 대한 썸네일 이미지 내용을 제안하고 첫 2분 스크립트를 작성해 주세요.
제목은 시청자들의 관심을 끌 수 있도록 매력적으로 작성해주세요.
스크립트는 시청자들의 관심을 유지하고, 친근하면서 전문적인 대화체로 긍정 반응을 유도하는 어휘, 질문, 콜 투 액션을 포함하여 영상의 주제를 명확하게 소개해야 합니다.
썸네일은 인기 영상 썸네일 스타일과 감정 분석 결과를 반영하고, 간결하고 강렬한 메시지와 주제에 맞는 디자인 요소를 포함해야 합니다. 또한 핵심 인물/감정을 클로즈업할 필요가 있습니다.

채널 정보: {channel_summary}

인기 동영상 정보:
{video_summary}

시청자 댓글 감정 분석:
{sentiment_summary}

반드시 아래처럼 대괄호로 구분하는 형식을 정확히 지키면서 응답해주세요.
[제목]
제목 3가지

[썸네일]
썸네일 3가지

[스크립트]
첫 2분 스크립트 내용
    """
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            response = client.chat.completions.create(
                model="gpt-4o-2024-08-06",
                messages=[
                    {"role": "system", "content": "당신은 유튜브 콘텐츠 크리에이터와 마케팅 전문가입니다. 매력적인 제목과 썸네일, 그리고 사람들이 계속 시청하게 만드는 스크립트를 작성하는 전문가입니다."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,  # 창의성을 위한 온도 설정
                max_tokens=1000  # 충분한 스크립트 길이
            )
            
            # 응답에서 텍스트 추출
            content_result = response.choices[0].message.content.strip()

            title_list = []
            thumbnail_list = []
            script = ""
            
            # 제목과 썸네일 추출
            title_section = re.search(r'\[제목\]\s*(.+?)(?=\[|$)', content_result, re.DOTALL)
            if title_section:
                # 텍스트를 줄바꿈으로 나누고 빈 줄 제거
                title_list = [line.strip() for line in title_section.group(1).split('\n') if line.strip()]
                
            thumbnail_section = re.search(r'\[썸네일\]\s*(.+?)(?=\[|$)', content_result, re.DOTALL)
            if thumbnail_section:
                # 텍스트를 줄바꿈으로 나누고 빈 줄 제거
                thumbnail_list = [line.strip() for line in thumbnail_section.group(1).split('\n') if line.strip()]
            
            # 스크립트 추출
            script_match = re.search(r'\[스크립트\]\s*(.+?)$', content_result, re.DOTALL)
            if script_match:
                script = script_match.group(1).strip()
            
            # 누락된 항목을 빈 문자열로 채우기
            while len(title_list) < 3:
                title_list.append("")
                
            while len(thumbnail_list) < 3:
                thumbnail_list.append("")
            
            # 제목과 썸네일을 묶어서 리스트로 만들기
            title_thumbnail_pairs = []
            for i in range(min(len(title_list), len(thumbnail_list))):
                title_thumbnail_pairs.append({
                    "title": title_list[i],
                    "thumbnail": thumbnail_list[i]
                })
            
            return {
                'keyword': keyword,
                'content': content_result, 
                'title': title_list, 
                'thumbnail': thumbnail_list, 
                'script': script
            }
            
        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                return {
                    'keyword': keyword,
                    'content': f"콘텐츠 생성 중 오류 발생: {str(e)}", 
                    'title': [], 
                    'thumbnail': [], 
                    'script': ""
                }
    
    return {
        'keyword': keyword,
        'content': "콘텐츠 생성을 수행할 수 없습니다.", 
        'title': [], 
        'thumbnail': [], 
        'script': ""
    }

# PostgreSQL에 접속
def connect_postgres():
    conn = psycopg2.connect(
        host="15.164.112.237", 
        database="dify", 
        user="difyuser", 
        password="bico0218"
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    
    return conn

# 만 단위로 변환환
def format_to_10k(n):
    num = round(n / 10000, 1)  # 소수점 첫째자리에서 반올림
    return f"{num}만"


def main():
    st.title("유튜브 채널 분석기")
    
    api_key = YOUTUBE_API_KEY
    
    channel_url = st.text_input("YouTube Channel URL (e.g., https://youtube.com/@channelname)")
    keyword = st.text_input("동영상 제작에 사용할 키워드를 입력하세요")
    
    if api_key and channel_url:
        try:
            analyzer = YouTubeAnalyzer(api_key)
            
            with st.spinner("분석 중..."):
                # Get channel info
                channel_id = analyzer.get_channel_id(channel_url)
                channel_info = analyzer.get_channel_stats(channel_id)
                
                # Display channel information
                col1, col2 = st.columns([1, 3])
                with col1:
                    response = requests.get(channel_info['thumbnail'])
                    img = Image.open(BytesIO(response.content))
                    st.image(img, width=200)
                
                with col2:
                    st.subheader(channel_info['title'])
                    st.metric("구독자 수", f"{channel_info['subscribers']:,}")
                
                # 모든 영상 정보 가져오기
                videos = analyzer.get_all_videos(channel_id)
                df = pd.DataFrame(videos)
                df['views_subscriber_ratio'] = (df['views'] / channel_info['subscribers'] * 100).round(2)
                
                # Sort by views/subscriber ratio
                df = df.sort_values('views_subscriber_ratio', ascending=False).head(10).copy()
                
                # 각 영상의 롱폼/쇼츠 여부 추가 ("쇼츠"면 쇼츠, 아니면 롱폼)
                df['롱폼/쇼츠 여부'] = df['video_id'].apply(lambda vid: "쇼츠" if is_youtubeshorts(vid) else "롱폼")

                # 각 영상의 첫 3분 스크립트 추가
                with st.spinner("영상 스크립트 수집 중..."):
                    df['스크립트'] = df['video_id'].apply(lambda vid: youtube_transcript(vid))

                # Display videos in a table
                st.subheader("조회수/구독자 비율 TOP 10 영상")
                
                # Create a custom dataframe for display
                display_df = df[['thumbnail', 'title', 'views', 'views_subscriber_ratio', 'like_count', 'comment_count', '롱폼/쇼츠 여부', '스크립트']].copy()
                
                # Format numbers
                display_df['views'] = display_df['views'].apply(format_to_10k)
                display_df['views_subscriber_ratio'] = display_df['views_subscriber_ratio'].apply(lambda x: f"{int(round(x))}%")
                
                # Rename columns for display
                display_df.columns = ['썸네일', '제목', '조회수', '조회수/구독자 비율', '좋아요 수', '댓글 수', '롱폼/쇼츠 여부', '스크립트 (첫 3분)']

                # Display the table
                st.dataframe(
                    display_df,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(width="medium", help="영상 썸네일"),
                        "제목": st.column_config.Column(width="large"),
                        "조회수": st.column_config.Column(width="small"),
                        "조회수/구독자 비율": st.column_config.Column(width="small"),
                        "좋아요 수": st.column_config.Column(width="small"),
                        "댓글 수": st.column_config.Column(width="small"),
                        "롱폼/쇼츠 여부": st.column_config.Column(width="small"), 
                        "스크립트 (첫 3분)": st.column_config.TextColumn(width="large")
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=600
                )
                print("조회수/구독자 비율 TOP 10 영상\n", display_df)
                
                # 인기 댓글 보여주기 섹션 추가
                st.subheader("TOP 10 영상의 인기 댓글")
                
                # 각 동영상별로 댓글 가져오기
                video_comments = {}
                with st.spinner("모든 영상의 댓글 로딩 중..."):
                    for i, (_, row) in enumerate(df.iterrows()):
                        video_id = row['video_id']
                        video_title = row['title']
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        
                        # 인기 댓글 가져오기
                        comments = analyzer.get_top_comments(video_id)
                        
                        # 동영상 별로 댓글 저장
                        if video_title not in video_comments:
                            video_comments[video_title] = {
                                'title': video_title,
                                'url': video_url,
                                'comments': []
                            }
                        
                        # 댓글 저장
                        for comment in comments:
                            video_comments[video_title]['comments'].append(comment['text'])
                
                # 그룹화된 댓글 테이블 생성
                comments_data = []
                for video_title, data in video_comments.items():
                    comments = data['comments']
                    url = data['url']
                    
                    if comments:
                        # 모든 댓글을 하나의 문자열로 합치기
                        all_comments_text = ""
                        for i, comment in enumerate(comments, 1):
                            all_comments_text += f"댓글 {i}:\n{comment}\n\n"  # 각 댓글 사이에 줄바꿈 두 번
                        
                        # 각 동영상당 하나의 행만 추가
                        comments_data.append({
                            '영상 제목': video_title,
                            '링크': url,
                            '댓글 내용': all_comments_text.strip()
                        })

                # 댓글 데이터 테이블 표시
                if comments_data:
                    comments_df = pd.DataFrame(comments_data)
                    
                    # 테이블로 표시
                    st.dataframe(
                        comments_df,
                        column_config={
                            "영상 제목": st.column_config.Column(width="large"),
                            "링크": st.column_config.LinkColumn(width="medium"),
                            "댓글 내용": st.column_config.TextColumn(width="large"),
                        },
                        hide_index=True,
                        use_container_width=True,
                        height=600
                    )
                    print("TOP 10 영상의 인기 댓글\n", comments_df)
                else:
                    st.info("댓글을 가져올 수 없습니다.")
                    print("댓글을 가져올 수 없습니다.")
                
                # 감정 분석 수행 (선택 사항)
                st.subheader("각 동영상 댓글의 감정 분석")
                
                # 감정 분석 진행 상황 표시
                sentiment_progress = st.progress(0)
                sentiment_status = st.empty()
                
                # 감정 분석 결과 저장
                sentiment_results = []
                
                # 각 동영상별 감정 분석 수행
                for i, (video_title, data) in enumerate(video_comments.items()):
                    sentiment_status.text(f"분석 중: {video_title}")
                    sentiment_progress.progress((i + 1) / len(video_comments))
                    
                    # 분석할 댓글 준비
                    comments_for_analysis = []
                    for j, text in enumerate(data['comments']):
                        comments_for_analysis.append({
                            'author': f'작성자{j+1}',
                            'text': text,
                            'like_count': 0,
                        })
                    
                    # 감정 분석 수행
                    analysis = analyze_comments_sentiment(client, comments_for_analysis, video_title)
                    
                    # 결과 저장
                    sentiment_results.append({
                        "영상 제목": video_title,
                        "감정 분석": analysis
                    })
                
                # 진행 상태 제거
                sentiment_progress.empty()
                sentiment_status.empty()
                
                # 감정 분석 결과 테이블 표시
                sentiment_df = pd.DataFrame(sentiment_results)
                st.dataframe(
                    sentiment_df,
                    column_config={
                        "영상 제목": st.column_config.Column(width="large"),
                        "감정 분석": st.column_config.TextColumn(width="large"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
                print("동영상 댓글의 감정 분석\n", sentiment_df)

                # 키워드 기반 콘텐츠 생성
                if keyword:
                    st.subheader(f"'{keyword}'을/를 주제로한 유튜브 콘텐츠 제안")
                    
                    with st.spinner("유튜브 콘텐츠 생성 중..."):
                        content_result = generate_video_content(client, keyword, channel_info, df.to_dict('records'), sentiment_results)

                    # st.markdown(content_result['content'])
                    if content_result['title'] and content_result['thumbnail']:
                        # 제목과 썸네일 설명을 DataFrame으로 변환
                        tt_list = []  # title and thumbnail
                        for i in range(min(len(content_result['title']), len(content_result['thumbnail']))):
                            tt_list.append({
                                '제목': content_result['title'][i],
                                '썸네일 설명': content_result['thumbnail'][i]
                            })
                        
                        tt_df = pd.DataFrame(tt_list)
                        st.dataframe(
                            tt_df,
                            column_config={
                                "제목": st.column_config.Column(width="medium"),
                                "썸네일 설명": st.column_config.TextColumn(width="large"),
                            },
                            hide_index=True,
                            use_container_width=True
                        )
                        print('제목 및 썸네일:\n', tt_df)
                    
                    # 스크립트 표시
                    if content_result['script']:
                        st.subheader("추천 스크립트 (첫 2분)")
                        st.markdown(content_result['script'])
                        print("추천 스크립트 (첫 2분)\n", content_result['script'])
                    
                    # 썸네일 이미지 표시 (임시적으로 첫 번째 썸네일 설명만 이미지화)
                    if content_result['thumbnail'][0]:
                        st.subheader("썸네일 이미지")
                        thumbnail_prompt = content_result['thumbnail'][0] + '''
내용을 반영한 유튜브 동영상의 썸네일을 준비하려고 해. 다음을 고려해서 만들어줘.

텍스트:
- 잘 보이도록 크고 가독성 좋게
- 핵심 키워드가 들어가도록

이미지:
- 텍스트보다 조금 어둡게
- 텍스트랑 분위기가 맞게

무엇보다도 가장 중요한 것은 다음이야:
- 타겟층 고려
- 이미지만 보고도 어떤 내용이 담겨있을지 추측 가능
'''  # content_result['thumbnail'][i]
                        thumbnail_img = client.images.generate(
                            model='dall-e-3', 
                            prompt=thumbnail_prompt, 
                            size='1792x1024', 
                            quality='standard', 
                            n=1, 
                        )

                        image_response = requests.get(thumbnail_img.data[0].url)

                        if image_response.status_code == 200:
                            # 바이트 스트림에서 이미지 객체 생성
                            img = Image.open(BytesIO(image_response.content))
                            # st.dataframe 대신 st.image 사용
                            st.image(img, caption="DALL-E로 생성된 썸네일 이미지", use_column_width=True)
                            
                            # 이미지 URL을 표시 (선택 사항)
                            st.markdown(f"[고해상도 이미지 링크]({thumbnail_img.data[0].url})")
                else:
                    st.warning("키워드를 입력하세요.")
                
                # Postgres에 저장
                conn = connect_postgres()
                cur = conn.cursor()
                
                insert_query = '''
                INSERT INTO youtube_analysis_results (channel_url, keyword, llm_response)
                VALUES (%s, %s, %s)
                RETURNING id;
                '''

                cur.execute(insert_query, (channel_url, keyword, content_result['content']))
        
        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")
    

if __name__ == "__main__":
    main()
