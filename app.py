import streamlit as st
import pandas as pd
import os
from dotenv import load_dotenv

from googleapiclient.discovery import build

from openai import OpenAI

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# 커스텀 모듈
from youtube import is_youtubeshorts, youtube_transcript
from saveNload import save_info, load_info, fetch_youtube_data, get_top_videos_by_search_id, save_video_analysis, save_thumbnail_analysis
from extract_blog_content import blog_content
from analyse_video import analyze_channel_video, analyze_keyword_video, analyze_thumbnails
from feedback import save_feedback


st.set_page_config(page_title="유튜브 채널 분석기", layout="wide")

load_dotenv()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai_client = OpenAI(api_key=OPENAI_API_KEY)

llm_option = st.selectbox("LLM 선택", ('gpt-4o-2024-08-06', 'gpt-4o-mini-2024-07-18', 'gpt-3.5-turbo-0125'))  # 'o1-mini-2024-09-12'


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

def search_unique_id():
    conn = connect_postgres()
    cur = conn.cursor()
    
    # 시퀀스가 없으면 생성
    cur.execute("CREATE SEQUENCE IF NOT EXISTS youtube_search_seq")
    
    # 다음 시퀀스 값 가져오기
    cur.execute("SELECT nextval('youtube_search_seq')")
    search_id = cur.fetchone()[0]
    
    cur.close()
    conn.close()
    
    return search_id

# 만 단위로 변환
def format_to_10k(n):
    num = round(n / 10000, 1)  # 소수점 첫째자리에서 반올림
    return f"{num}만"

# 유튜브 채널 데이터 수집
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
            
            if not video_ids:
                break
            
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

def blog_summarizer(client, llm, text):
    try:
        # 입력 텍스트가 너무 길 경우 제한 (API 제한을 고려)
        if len(text) > 15000:
            text = text[:15000] + "..."
    
        summary = client.chat.completions.create(
            model=llm,  # 'gpt-4o-2024-08-06'
            messages=[
                {"role": "system", "content": "다음 블로그 포스트를 명확하고 간결하게 요약해주세요. 핵심 내용과 주요 포인트를 포함시켜야 합니다."},
                {"role": "user", "content": text}
            ],
            temperature=0.3, 
            max_tokens=500,
        )
        
        return summary.choices[0].message.content.strip()
    
    except Exception as e:
        return {"블로그 내용 요약 중 오류가 발생했습니다.": str(e)}


# # 메인 탭 # #
st.title("유튜브 채널 분석기")
tab1, tab3, tab2, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "채널 데이터 수집", "채널 데이터 조회", "검색어 데이터 수집", "키워드 데이터 조회", 
    "블로그 분석기", "블로그 통합 분석", 
    "컨텐츠 만들기", "컨텐츠 평가", 
    "썸네일 분석 내용 정리"
])

# 탭 1: 채널 데이터 수집 탭
with tab1:
    st.subheader("채널 데이터 수집")
    channel_url = st.text_input("유튜브 채널 주소 (e.g., https://youtube.com/@channelname)")
    keyword = st.text_input("동영상 제작에 사용할 키워드를 입력하세요")
    submit_button = st.button("데이터 수집 및 저장", type="primary")

    if submit_button and channel_url and keyword:
        try:
            with st.spinner("채널 정보를 가져오는 중..."):
                analyzer = YouTubeAnalyzer(YOUTUBE_API_KEY)
                
                # 고유 검색 ID 생성 (PostgreSQL에서 자동으로 생성)
                pk_id = search_unique_id()
                
                # 채널 ID 가져오기
                channel_id = analyzer.get_channel_id(channel_url)
                
                # 채널 통계 가져오기
                channel_stats = analyzer.get_channel_stats(channel_id)
                
                # 채널 정보 출력
                st.subheader(f"채널: {channel_stats['title']}")
                col1, col2 = st.columns([1, 3])
                with col1:
                    st.image(channel_stats['thumbnail'], width=150)
                with col2:
                    st.write(f"구독자: {format_to_10k(channel_stats['subscribers'])} ({channel_stats['subscribers']}명)")
                
                # 동영상 정보 가져오기
                videos = analyzer.get_all_videos(channel_id)
                
                # 상위 10개 동영상만 처리 (또는 전체 동영상이 10개 미만인 경우)
                top_videos = videos[:10]
                
                st.subheader(f"상위 {len(top_videos)}개 동영상 분석 및 저장 중...")
                progress_bar = st.progress(0)
                
                # 각 동영상 정보 처리 및 저장
                for i, video in enumerate(top_videos):
                    video_id = video['video_id']
                    
                    is_shorts = is_youtubeshorts(video_id)  # 쇼츠 여부 확인
                    transcript = youtube_transcript(video_id)  # 자막 가져오기
                    view_subscriber_ratio = video['views'] / channel_stats['subscribers'] if channel_stats['subscribers'] > 0 else 0  # 조회수/구독자 비율 계산
                    comments = analyzer.get_top_comments(video_id, 3)  # 댓글 가져오기
                    
                    save_info(
                        'channel_info', pk_id, keyword, channel_url, channel_stats['title'], channel_stats['subscribers'], 
                        video_id, video['title'], video['thumbnail'], video['views'], video['like_count'], video['comment_count'], view_subscriber_ratio,
                        is_shorts, transcript, video['published_at'], comments
                    )
                    
                    progress_bar.progress((i + 1) / len(top_videos))  # 진행상황 업데이트
                
                st.success(f"성공적으로 채널 '{channel_stats['title']}'의 데이터를 저장했습니다!")

        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")
    elif submit_button:
        st.warning("모든 필드를 입력해주세요: 채널 URL, 키워드, 검색 ID")

# 탭 3: 채널 데이터 조회 탭
with tab3:
    st.subheader("저장된 채널 데이터 조회")
    
    # 세션 상태 초기화
    if 'search_clicked_tab3' not in st.session_state:
        st.session_state.search_clicked_tab3 = False
    if 'shorts_analyzed_tab3' not in st.session_state:
        st.session_state.shorts_analyzed_tab3 = False
    if 'longform_analyzed_tab3' not in st.session_state:
        st.session_state.longform_analyzed_tab3 = False
    if 'shorts_analysis_result_tab3' not in st.session_state:
        st.session_state.shorts_analysis_result_tab3 = None
    if 'longform_analysis_result_tab3' not in st.session_state:
        st.session_state.longform_analysis_result_tab3 = None
    if 'shorts_thumbnail_analysis_tab3' not in st.session_state:  # 추가
        st.session_state.shorts_thumbnail_analysis_tab3 = None
    if 'longform_thumbnail_analysis_tab3' not in st.session_state:  # 추가
        st.session_state.longform_thumbnail_analysis_tab3 = None
    if 'found_data_tab3' not in st.session_state:
        st.session_state.found_data_tab3 = None
    
    # 먼저 모든 채널별 최고 성과 동영상 표시
    st.subheader("검색ID별 최고 성과 동영상 목록")
    
    try:
        top_videos_df = get_top_videos_by_search_id('channel_info')
        
        if not top_videos_df.empty:
            # 데이터 표시
            st.dataframe(
                top_videos_df,
                column_config={
                    "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                    "검색ID": st.column_config.Column(width="small", help="이 ID를 아래 입력란에 입력하여 상세 분석"),
                    "키워드": st.column_config.Column(width="medium"),
                    "채널명": st.column_config.Column(width="medium"), 
                    "제목": st.column_config.Column(width="large"),
                    "조회수": st.column_config.Column(width="small"),
                    "좋아요": st.column_config.Column(width="small"),
                    "댓글수": st.column_config.Column(width="small"),
                    "조회수/구독자 비율": st.column_config.Column(width="small"),
                    "쇼츠": st.column_config.Column(width="small")
                },
                hide_index=True,
                use_container_width=True,
                height=300
            )
            
            # ID 선택에 도움이 되는 정보 추가
            st.info("👆 위 목록에서 상세 분석하고 싶은 검색ID를 확인하고, 아래에 입력하세요.")
        else:
            st.warning("저장된 채널 데이터가 없습니다.")
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
    
    # 구분선 추가
    st.markdown("---")
    
    # 특정 채널 상세 분석 섹션
    st.subheader("특정 검색ID 상세 분석")
    search_id_input = st.number_input("분석할 검색 ID를 입력하세요", min_value=1, step=1)
    
    # 검색 버튼 콜백
    def on_search_click_tab3():
        st.session_state.search_clicked_tab3 = True
        st.session_state.shorts_analyzed_tab3 = False
        st.session_state.longform_analyzed_tab3 = False
        st.session_state.shorts_analysis_result_tab3 = None
        st.session_state.longform_analysis_result_tab3 = None
        st.session_state.shorts_thumbnail_analysis_tab3 = None  # 추가
        st.session_state.longform_thumbnail_analysis_tab3 = None  # 추가
        st.session_state.found_data_tab3 = None  # 새 검색 시 데이터 초기화
        
    # 쇼츠 분석 버튼 콜백
    def on_analyze_shorts_click_tab3():
        st.session_state.shorts_analyzed_tab3 = True
    
    # 롱폼 분석 버튼 콜백
    def on_analyze_longform_click_tab3():
        st.session_state.longform_analyzed_tab3 = True
    
    search_button = st.button("검색", type="primary", key="search_button_tab3", on_click=on_search_click_tab3)
    
    # 검색 결과 표시
    if st.session_state.search_clicked_tab3:
        try:
            if 'found_data_tab3' not in st.session_state or st.session_state.found_data_tab3 is None:
                display_df = load_info(search_id_input, 'channel_info')
                st.session_state.found_data_tab3 = display_df
            else:
                display_df = st.session_state.found_data_tab3
            
            if not display_df.empty:
                st.success(f"검색 ID {search_id_input}에 해당하는 데이터를 찾았습니다.")
                
                # 쇼츠와 롱폼 영상 분리해서 통계 표시
                shorts_df = display_df[display_df['쇼츠'] == True]
                longform_df = display_df[display_df['쇼츠'] == False]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("쇼츠 영상")
                    st.write(f"영상 수: {len(shorts_df)}")
                    if not shorts_df.empty:
                        st.write(f"평균 조회수: {shorts_df['조회수'].mean():.1f}")
                        st.write(f"평균 좋아요: {shorts_df['좋아요'].mean():.1f}")
                
                with col2:
                    st.subheader("롱폼 영상")
                    st.write(f"영상 수: {len(longform_df)}")
                    if not longform_df.empty:
                        st.write(f"평균 조회수: {longform_df['조회수'].mean():.1f}")
                        st.write(f"평균 좋아요: {longform_df['좋아요'].mean():.1f}")
                
                # 전체 데이터 표시
                st.subheader("모든 영상 데이터")
                st.dataframe(
                    display_df,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                        "채널명": st.column_config.Column(width="medium"), 
                        "제목": st.column_config.Column(width="large"),
                        "조회수": st.column_config.Column(width="small"),
                        "좋아요": st.column_config.Column(width="small"),
                        "댓글수": st.column_config.Column(width="small"),
                        "조회수/구독자 비율": st.column_config.Column(width="small"),
                        "쇼츠": st.column_config.Column(width="small"), 
                        "댓글1": st.column_config.Column(width="large"), 
                        "댓글2": st.column_config.Column(width="large"), 
                        "댓글3": st.column_config.Column(width="large"), 
                        "스크립트": st.column_config.TextColumn(width="large")
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=600
                )
                
                # 분석 섹션
                st.subheader("채널 데이터 분석하기")
                
                # 1. 쇼츠 분석 섹션
                st.write("### 쇼츠 영상 분석")
                
                if len(shorts_df) == 0:
                    st.info("해당 채널에는 쇼츠가 없습니다.")
                else:
                    # 쇼츠 분석 버튼
                    if not st.session_state.shorts_analyzed_tab3:
                        shorts_btn = st.button(
                            "쇼츠 분석 시작", 
                            type="primary", 
                            key="btn_analyze_shorts",
                            on_click=on_analyze_shorts_click_tab3
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.shorts_analyzed_tab3:
                        if st.session_state.shorts_analysis_result_tab3 is None:
                            with st.spinner("쇼츠 영상 분석 중..."):
                                # 쇼츠 분석 수행
                                shorts_analysis = analyze_channel_video(openai_client, llm_option, display_df, is_shorts=True)
                                st.session_state.shorts_analysis_result_tab3 = shorts_analysis

                                thumbnail_analysis_shorts = analyze_thumbnails(openai_client, display_df, is_shorts=True)
                                st.session_state.shorts_thumbnail_analysis_tab3 = thumbnail_analysis_shorts
                                
                                # 분석 내용 저장
                                save_video_analysis('channel_analysis', search_id_input, True, shorts_analysis)

                                save_thumbnail_analysis(thumbnail_analysis_shorts, search_id_input, True, display_df['채널URL'].iloc[0])
                                
                                st.success("쇼츠 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.shorts_analysis_result_tab3)

                        # 썸네일 분석 결과 표시
                        st.write("### 인기 썸네일 분석")
                        if isinstance(st.session_state.shorts_thumbnail_analysis_tab3, list):
                            for analysis in st.session_state.shorts_thumbnail_analysis_tab3:
                                st.write(f"#### {analysis['제목']}")
                                cols = st.columns([1, 2])
                                with cols[0]:
                                    st.image(analysis['썸네일'])
                                with cols[1]:
                                    st.write(analysis['분석'])
                        else:
                            st.write(st.session_state.shorts_thumbnail_analysis_tab3)
                
                # 구분선
                st.markdown("---")
                
                # 2. 롱폼 분석 섹션
                st.write("### 롱폼 영상 분석")
                
                if len(longform_df) == 0:
                    st.info("해당 채널에는 롱폼이 없습니다.")
                else:
                    # 롱폼 분석 버튼
                    if not st.session_state.longform_analyzed_tab3:
                        longform_btn = st.button(
                            "롱폼 분석 시작", 
                            type="primary", 
                            key="btn_analyze_longform",
                            on_click=on_analyze_longform_click_tab3
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.longform_analyzed_tab3:
                        if st.session_state.longform_analysis_result_tab3 is None:
                            with st.spinner("롱폼 영상 분석 중..."):
                                # 롱폼 분석 수행
                                longform_analysis = analyze_channel_video(openai_client, llm_option, display_df, is_shorts=False)
                                st.session_state.longform_analysis_result_tab3 = longform_analysis
                                
                                thumbnail_analysis_long = analyze_thumbnails(openai_client, display_df, is_shorts=False)
                                st.session_state.longform_thumbnail_analysis_tab3 = thumbnail_analysis_long

                                # 분석 내용 저장
                                save_video_analysis('channel_analysis', search_id_input, False, longform_analysis)

                                save_thumbnail_analysis(thumbnail_analysis_long, search_id_input, False, display_df['채널URL'].iloc[0])
                                
                                st.success("롱폼 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.longform_analysis_result_tab3)

                        st.write("### 인기 썸네일 분석")
                        if isinstance(st.session_state.longform_thumbnail_analysis_tab3, list):
                            for analysis in st.session_state.longform_thumbnail_analysis_tab3:
                                st.write(f"#### {analysis['제목']}")
                                cols = st.columns([1, 2])
                                with cols[0]:
                                    st.image(analysis['썸네일'])
                                with cols[1]:
                                    st.write(analysis['분석'])
                        else:
                            st.write(st.session_state.longform_thumbnail_analysis_tab3)
            else:
                st.warning(f"검색 ID {search_id_input}에 해당하는 데이터가 없습니다.")
                st.session_state.found_data_tab3 = None
        except Exception as e:
            st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
            st.session_state.found_data_tab3 = None

# 탭 2: 키워드 기반 데이터 수집 탭
with tab2:
    st.subheader("검색어 데이터 수집")
    
    query = st.text_input("분석하고 싶은 키워드를 입력하세요:")
    max_results = st.slider("분석할 영상 수", 10, 50, 30)
    search_button = st.button("검색 시작", type="primary")

    if query and search_button:
        with st.spinner("데이터를 수집하고 분석 중입니다..."):
            try:
                # 검색 고유 ID 생성
                pk_id = search_unique_id()

                df = fetch_youtube_data(query, max_results)
                
                st.subheader("📊 기본 통계")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("총 조회수", f"{df['views'].sum():,}")
                with col2:
                    st.metric("평균 좋아요", f"{int(df['likes'].mean()):,}")
                with col3:
                    st.metric("평균 댓글", f"{int(df['comments'].mean()):,}")
                
                # 검색 결과를 데이터베이스에 저장
                progress_bar = st.progress(0)
                st.text("검색 결과를 데이터베이스에 저장 중...")
                
                # YouTube API 객체 생성 (댓글 정보를 가져오기 위함)
                analyzer = YouTubeAnalyzer(YOUTUBE_API_KEY)
                
                # 각 동영상 정보 처리 및 저장
                for i, (_, video) in enumerate(df.iterrows()):
                    video_id = video['url'].split('v=')[1] if 'v=' in video['url'] else video['url'].split('/')[-1]
                    
                    # 댓글 정보 가져오기
                    comments = analyzer.get_top_comments(video_id, 3)
                    
                    # channel_url 생성 (채널 이름으로부터)
                    channel_url = f"https://www.youtube.com/channel/{video_id}"
                    
                    # 데이터베이스에 저장
                    save_info(
                        'keyword_info', pk_id, query, channel_url, video['channel'], video['subscribers'],
                        video_id, video['title'], video['thumbnail'], video['views'], video['likes'], video['comments'], video['view_sub_ratio'],
                        video['is_shorts'], video['1min_script'], video['publishedAt'], comments
                    )
                    
                    # 진행 상황 업데이트
                    progress_bar.progress((i + 1) / len(df))
                
                st.success(f"성공적으로 키워드 '{query}'에 대한 {len(df)}개의 동영상 데이터를 저장했습니다! (검색 ID: {pk_id})")
                
                st.subheader("🔥 인기 영상 TOP 5")
                top_videos = df.nlargest(5, 'views')
                top_videos['views'] = top_videos['views'].apply(lambda x: format_to_10k(x) + " 건")
                top_videos['subscribers'] = top_videos['subscribers'].apply(lambda x: format_to_10k(x) + " 명")
                top_videos['view_sub_ratio'] = top_videos['view_sub_ratio'].apply(lambda x: f"{round(x)}%")
                top_videos['thumbnail'] = top_videos.apply(lambda x: f'<a href="{x["url"]}" target="_blank"><img src="{x["thumbnail"]}" width="240"/></a>', axis=1)
                display_videos = top_videos[['thumbnail', 'title', 'channel', 'views', 'subscribers', 'view_sub_ratio', 'is_shorts', '1min_script']]
                display_videos.columns = ['썸네일', '제목', '채널명', '조회수', '구독자수', '조회수/구독자 비율', '쇼츠', '최초 3분 스크립트']
                display_videos['쇼츠'] = display_videos['쇼츠'].map({True: '쇼츠', False: '롱폼'})
                st.markdown(display_videos.to_html(escape=False, index=False), unsafe_allow_html=True)
                
                df['engagement_rate'] = (df['likes'] + df['comments']) / df['views'] * 100
                st.subheader("📈 참여도 분석")
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("평균 참여율", f"{df['engagement_rate'].mean():.2f}%")
                with col2:
                    st.metric("최고 참여율", f"{df['engagement_rate'].max():.2f}%")
            except Exception as e:
                st.error(f"오류가 발생했습니다: {str(e)}")

# 탭 4: 키워드 데이터 조회 탭
with tab4:
    st.subheader("저장된 키워드 데이터 조회")
    
    # 세션 상태 초기화
    if 'search_clicked_tab4' not in st.session_state:
        st.session_state.search_clicked_tab4 = False
    if 'shorts_analyzed_tab4' not in st.session_state:  # 쇼츠 분석 완료 여부
        st.session_state.shorts_analyzed_tab4 = False
    if 'longform_analyzed_tab4' not in st.session_state:  # 롱폼 분석 완료 여부
        st.session_state.longform_analyzed_tab4 = False
    if 'shorts_analysis_result_tab4' not in st.session_state:  # 쇼츠 분석 결과
        st.session_state.shorts_analysis_result_tab4 = None
    if 'longform_analysis_result_tab4' not in st.session_state:  # 롱폼 분석 결과
        st.session_state.longform_analysis_result_tab4 = None
    if 'shorts_thumbnail_analysis_tab4' not in st.session_state:  # 추가
        st.session_state.shorts_thumbnail_analysis_tab4 = None
    if 'longform_thumbnail_analysis_tab4' not in st.session_state:  # 추가
        st.session_state.longform_thumbnail_analysis_tab4 = None
    if 'found_data_tab4' not in st.session_state:
        st.session_state.found_data_tab4 = None
    
    # 먼저 모든 채널별 최고 성과 동영상 표시
    st.subheader("검색ID별 최고 성과 동영상 목록")
    
    try:
        top_videos_df = get_top_videos_by_search_id('keyword_info')
        
        if not top_videos_df.empty:
            # 데이터 표시
            st.dataframe(
                top_videos_df,
                column_config={
                    "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                    "검색ID": st.column_config.Column(width="small", help="이 ID를 아래 입력란에 입력하여 상세 분석"),
                    "키워드": st.column_config.Column(width="medium"),
                    "채널명": st.column_config.Column(width="medium"), 
                    "제목": st.column_config.Column(width="large"),
                    "조회수": st.column_config.Column(width="small"),
                    "좋아요": st.column_config.Column(width="small"),
                    "댓글수": st.column_config.Column(width="small"),
                    "조회수/구독자 비율": st.column_config.Column(width="small"),
                    "쇼츠": st.column_config.Column(width="small")
                },
                hide_index=True,
                use_container_width=True,
                height=300
            )
            
            # ID 선택에 도움이 되는 정보 추가
            st.info("👆 위 목록에서 상세 분석하고 싶은 검색ID를 확인하고, 아래에 입력하세요.")
        else:
            st.warning("저장된 채널 데이터가 없습니다.")
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
    
    # 구분선 추가
    st.markdown("---")
    
    # 특정 채널 상세 분석 섹션
    st.subheader("특정 검색ID 상세 분석")
    search_id_input = st.number_input("키워드에 대해 조회할 검색 ID를 입력하세요", min_value=1, step=1)
    
    # 검색 버튼 콜백
    def on_search_click_tab4():
        st.session_state.search_clicked_tab4 = True
        st.session_state.shorts_analyzed_tab4 = False
        st.session_state.longform_analyzed_tab4 = False
        st.session_state.shorts_analysis_result_tab4 = None
        st.session_state.longform_analysis_result_tab4 = None
        st.session_state.shorts_thumbnail_analysis_tab4 = None  # 추가
        st.session_state.longform_thumbnail_analysis_tab4 = None  # 추가
        st.session_state.found_data_tab4 = None  # 새 검색 시 데이터 초기화
        
    # 쇼츠 분석 버튼 콜백
    def on_analyze_shorts_click_tab4():
        st.session_state.shorts_analyzed_tab4 = True
    
    # 롱폼 분석 버튼 콜백
    def on_analyze_longform_click_tab4():
        st.session_state.longform_analyzed_tab4 = True
    
    search_button_keyword = st.button("검색", type="primary", key="search_button_keyword_tab4", on_click=on_search_click_tab4)
    
    # 검색 결과 표시
    if st.session_state.search_clicked_tab4:
        try:
            if 'found_data_tab4' not in st.session_state or st.session_state.found_data_tab4 is None:
                display_df = load_info(search_id_input, 'keyword_info')
                st.session_state.found_data_tab4 = display_df
            else:
                display_df = st.session_state.found_data_tab4
            
            if not display_df.empty:
                st.success(f"검색 ID {search_id_input}에 해당하는 데이터를 찾았습니다.")
                
                # 쇼츠와 롱폼 영상 분리해서 통계 표시
                shorts_df = display_df[display_df['쇼츠'] == True]
                longform_df = display_df[display_df['쇼츠'] == False]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("쇼츠 영상")
                    st.write(f"영상 수: {len(shorts_df)}")
                    if not shorts_df.empty:
                        st.write(f"평균 조회수: {shorts_df['조회수'].mean():.1f}")
                        st.write(f"평균 좋아요: {shorts_df['좋아요'].mean():.1f}")
                
                with col2:
                    st.subheader("롱폼 영상")
                    st.write(f"영상 수: {len(longform_df)}")
                    if not longform_df.empty:
                        st.write(f"평균 조회수: {longform_df['조회수'].mean():.1f}")
                        st.write(f"평균 좋아요: {longform_df['좋아요'].mean():.1f}")
                
                # 전체 데이터 표시
                st.subheader("모든 영상 데이터")
                # st.dataframe(display_df, use_container_width=True)
                st.dataframe(
                    display_df,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(width="large", help="영상 썸네일"),
                        "채널명": st.column_config.Column(width="medium"), 
                        "제목": st.column_config.Column(width="large"),
                        "조회수": st.column_config.Column(width="small"),
                        "좋아요": st.column_config.Column(width="small"),
                        "댓글 수": st.column_config.Column(width="small"),
                        "조회수/구독자 비율": st.column_config.Column(width="small"),
                        "쇼츠": st.column_config.Column(width="small"), 
                        "댓글1": st.column_config.Column(width="large"), 
                        "댓글2": st.column_config.Column(width="large"), 
                        "댓글3": st.column_config.Column(width="large"), 
                        "스크립트": st.column_config.TextColumn(width="large")
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=600
                )
                
                # 분석 섹션
                st.subheader("채널 데이터 분석하기")
                
                # 1. 쇼츠 분석 섹션
                st.write("### 쇼츠 영상 분석")
                
                if len(shorts_df) == 0:
                    st.info("해당 채널에는 쇼츠가 없습니다.")
                else:
                    # 쇼츠 분석 버튼
                    if not st.session_state.shorts_analyzed_tab4:
                        shorts_btn = st.button(
                            "쇼츠 분석 시작", 
                            type="primary", 
                            key="btn_analyze_shorts_tab4",
                            on_click=on_analyze_shorts_click_tab4
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.shorts_analyzed_tab4:
                        if st.session_state.shorts_analysis_result_tab4 is None:
                            with st.spinner("쇼츠 영상 분석 중..."):
                                # 쇼츠 분석 수행
                                shorts_analysis = analyze_keyword_video(openai_client, display_df, is_shorts=True)
                                st.session_state.shorts_analysis_result_tab4 = shorts_analysis

                                thumbnail_analysis_shorts = analyze_thumbnails(openai_client, display_df, is_shorts=True)
                                st.session_state.shorts_thumbnail_analysis_tab4 = thumbnail_analysis_shorts
                                
                                # 분석 내용 저장
                                save_video_analysis('keyword_analysis', search_id_input, True, shorts_analysis)

                                save_thumbnail_analysis(thumbnail_analysis_shorts, search_id_input, True, display_df['채널URL'].iloc[0])
                                
                                st.success("쇼츠 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.shorts_analysis_result_tab4)

                        # 썸네일 분석 결과 표시
                        st.write("### 인기 썸네일 분석")
                        if isinstance(st.session_state.shorts_thumbnail_analysis_tab4, list):
                            for analysis in st.session_state.shorts_thumbnail_analysis_tab4:
                                st.write(f"#### {analysis['제목']}")
                                cols = st.columns([1, 2])
                                with cols[0]:
                                    st.image(analysis['썸네일'])
                                with cols[1]:
                                    st.write(analysis['분석'])
                        else:
                            st.write(st.session_state.shorts_thumbnail_analysis_tab4)
                
                # 구분선
                st.markdown("---")
                
                # 2. 롱폼 분석 섹션
                st.write("### 롱폼 영상 분석")
                
                if len(longform_df) == 0:
                    st.info("해당 채널에는 롱폼이 없습니다.")
                else:
                    # 롱폼 분석 버튼
                    if not st.session_state.longform_analyzed_tab4:
                        longform_btn = st.button(
                            "롱폼 분석 시작", 
                            type="primary", 
                            key="btn_analyze_longform_tab4",
                            on_click=on_analyze_longform_click_tab4
                        )
                    
                    # 분석 수행 및 결과 표시
                    if st.session_state.longform_analyzed_tab4:
                        if st.session_state.longform_analysis_result_tab4 is None:
                            with st.spinner("롱폼 영상 분석 중..."):
                                # 롱폼 분석 수행
                                longform_analysis = analyze_keyword_video(openai_client, llm_option, display_df, is_shorts=False)
                                st.session_state.longform_analysis_result_tab4 = longform_analysis

                                thumbnail_analysis_long = analyze_thumbnails(openai_client, display_df, is_shorts=False)
                                st.session_state.longform_thumbnail_analysis_tab4 = thumbnail_analysis_long
                                
                                # 분석 내용 저장
                                save_video_analysis('keyword_analysis', search_id_input, False, longform_analysis)

                                save_thumbnail_analysis(thumbnail_analysis_long, search_id_input, False, display_df['채널URL'].iloc[0])
                                
                                st.success("롱폼 영상 분석 완료 및 저장되었습니다!")
                        
                        # 저장된 분석 결과 표시
                        st.write(st.session_state.longform_analysis_result_tab4)

                        # 썸네일 분석 결과 표시
                        st.write("### 인기 썸네일 분석")
                        if isinstance(st.session_state.longform_thumbnail_analysis_tab4, list):
                            for analysis in st.session_state.longform_thumbnail_analysis_tab4:
                                st.write(f"#### {analysis['제목']}")
                                cols = st.columns([1, 2])
                                with cols[0]:
                                    st.image(analysis['썸네일'])
                                with cols[1]:
                                    st.write(analysis['분석'])
                        else:
                            st.write(st.session_state.longform_thumbnail_analysis_tab4)
            
        except Exception as e:
            st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
            st.session_state.found_data_tab4 = None

# 탭 5: 블로그 분석기 탭
with tab5:
    st.subheader("블로그 분석기")

    analysis_keyword = st.text_input('블로그 분석을 위한 키워드를 입력하세요. 분석 그룹의 이름을 결정합니다.')
    
    # # 블로그 한개 분석
    # blog_url = st.text_input('분석할 블로그 주소를 입력하세요.')  # 나중에 10개로 늘릴 예정
    # analyse_button = st.button("블로그 분석 시작", type="primary")
    
    # if blog_url and analysis_keyword and analyse_button:
    #     with st.spinner("블로그 내용을 분석 중입니다..."):
    #         try:
    #             extracted_data = blog_content(blog_url)

    #             blog_summary = blog_summarizer(openai_client, extracted_data['content'])

    #             conn = connect_postgres()
    #             cur = conn.cursor()

    #             # RETURNING 절 추가
    #             cur.execute("""
    #             INSERT INTO blog_summary (keyword, url, summary) 
    #             VALUES (%s, %s, %s) RETURNING id
    #             """, (analysis_keyword, blog_url, blog_summary))
                
    #             # 반환된 id 가져오기
    #             inserted_id = cur.fetchone()[0]

    #             conn.commit()
    #             cur.close()
    #             conn.close()

    #             st.subheader("블로그 요약 결과")
    #             st.write(blog_summary)
                
    #             st.success(f"블로그 분석 결과가 성공적으로 저장되었습니다! (ID: {inserted_id})")
    #         except Exception as e:
    #             st.error(f"블로그 분석 중 오류가 발생했습니다: {str(e)}")
    
    # # 블로그 여러개 분석
    blog_urls_container = st.container()  # 컨테이너 생성
    
    # 세션 상태에 URL 리스트 초기화
    if 'blog_urls' not in st.session_state:
        st.session_state.blog_urls = [""] * 10  # 10개의 빈 URL로 초기화
    
    # 5개씩 두 열로 나눠서 URL 입력 필드 생성
    col1, col2 = st.columns(2)
    
    # 왼쪽 컬럼 (0-4번 URL)
    with col1:
        for i in range(5):
            st.text_input(
                f"블로그 주소 {i+1}",
                value=st.session_state.blog_urls[i],
                key=f"blog_url_{i}"
            )
            # 입력 후 세션 상태 업데이트
            st.session_state.blog_urls[i] = st.session_state[f"blog_url_{i}"]
    
    # 오른쪽 컬럼 (5-9번 URL)
    with col2:
        for i in range(5, 10):
            st.text_input(
                f"블로그 주소 {i+1}",
                value=st.session_state.blog_urls[i],
                key=f"blog_url_{i}"
            )
            # 입력 후 세션 상태 업데이트
            st.session_state.blog_urls[i] = st.session_state[f"blog_url_{i}"]
    
    # 유효한 URL의 수 계산 (빈 문자열이 아닌 URL)
    valid_urls = [url for url in st.session_state.blog_urls if url.strip()]
    
    # 분석 버튼과 상태 표시
    analyse_button = st.button("블로그 분석 시작", type="primary", disabled=len(valid_urls) == 0 or not analysis_keyword)
    
    # 블로그 분석 실행
    if analysis_keyword and valid_urls and analyse_button:
        results_container = st.container()  # 분석 결과 저장용 컨테이너

        progress_bar = st.progress(0)  # 진행 상황 표시
        
        # 성공 및 실패 결과 수집
        success_count = 0
        failed_urls = []
        saved_ids = []
        
        # 각 URL 처리
        for i, url in enumerate(valid_urls):
            try:
                with st.spinner(f"블로그 분석 중... ({i+1}/{len(valid_urls)})"):
                    extracted_data = blog_content(url)  # 블로그 내용 추출
                    
                    blog_summary = blog_summarizer(openai_client, llm_option, extracted_data['content'])  # 블로그 요약
                    
                    # DB에 저장
                    conn = connect_postgres()
                    cur = conn.cursor()
                    
                    cur.execute("""INSERT INTO blog_summary (keyword, url, summary) VALUES (%s, %s, %s) RETURNING id""", (analysis_keyword, url, blog_summary))
                    
                    inserted_id = cur.fetchone()[0]
                    saved_ids.append(inserted_id)
                    
                    conn.commit()
                    cur.close()
                    conn.close()
                    
                    # 성공 카운트 증가
                    success_count += 1
                
            except Exception as e:
                # 실패한 URL 기록
                failed_urls.append((url, str(e)))
                
                # 에러 발생 시 DB 연결 닫기
                try:
                    if 'conn' in locals() and conn:
                        conn.rollback()
                        cur.close()
                        conn.close()
                except:
                    pass
            
            # 진행 상황 업데이트
            progress_bar.progress((i + 1) / len(valid_urls))
        
        # 분석 완료 후 결과 표시
        with results_container:
            st.subheader("블로그 분석 결과")
            
            if success_count > 0:
                st.success(f"{success_count}개의 블로그가 성공적으로 분석되었습니다.")
                
                # 저장된 결과 가져오기
                conn = connect_postgres()
                cur = conn.cursor()
                
                # IN 구문을 위한 문자열 생성
                ids_str = ",".join(str(id) for id in saved_ids)
                
                if ids_str:
                    cur.execute(f"""SELECT id, url, summary FROM blog_summary WHERE id IN ({ids_str}) ORDER BY id""")
                    results = cur.fetchall()
                    
                    cur.close()
                    conn.close()
                    
                    # 결과 표시
                    for result in results:
                        with st.expander(f"블로그: {result[1]}"):
                            st.markdown(f"**ID**: {result[0]}")
                            st.markdown("**요약**:")
                            st.write(result[2])
            
            # 실패한 URL 표시
            if failed_urls:
                st.error(f"{len(failed_urls)}개의 블로그 분석에 실패했습니다.")
                for url, error in failed_urls:
                    with st.expander(f"실패한 URL: {url}"):
                        st.error(f"오류: {error}")
   
# 탭 6: 블로그 통합 분석 탭
with tab6:
    st.subheader("블로그 통합 분석")
    
    # 모든 키워드별 블로그 요약 데이터 조회
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 키워드별로 그룹화하여 첫 번째 블로그 요약만 가져오기
        cur.execute("""
        WITH RankedSummaries AS (
            SELECT id, keyword, summary, url, ROW_NUMBER() OVER (PARTITION BY keyword ORDER BY id) as rn
            FROM blog_summary
        )
        SELECT id, keyword, summary, url FROM RankedSummaries
        WHERE rn = 1
        ORDER BY keyword
        """)
        
        keyword_summaries = cur.fetchall()
        cur.close()
        conn.close()
        
        if keyword_summaries:
            # 테이블용 데이터 준비
            table_data = []
            for summary_id, keyword, summary, url in keyword_summaries:
                # 요약 텍스트가 너무 길면 잘라내기
                short_summary = summary[:100] + "..." if len(summary) > 100 else summary
                table_data.append({"ID": summary_id, "키워드": keyword, "요약 미리보기": short_summary, "URL": url})
            
            # 데이터프레임으로 변환
            df = pd.DataFrame(table_data)
            
            # 테이블 표시
            st.dataframe(
                df,
                column_config={
                    "ID": st.column_config.Column(width="small"),
                    "키워드": st.column_config.Column(width="medium"),
                    "요약 미리보기": st.column_config.Column(width="large"),
                    "URL": st.column_config.Column(width="medium")
                },
                hide_index=True,
                use_container_width=True
            )
            
            # 상세 내용 보기 섹션
            selected_id = st.selectbox(
                "상세 내용을 확인할 키워드 ID를 선택하세요:",
                options=[row[0] for row in keyword_summaries],
                format_func=lambda x: f"ID: {x} - 키워드: {df[df['ID']==x]['키워드'].values[0]}"
            )
            
            if selected_id:
                # 선택한 ID의 요약 내용 표시
                selected_summary = next((s for s_id, k, s, u in keyword_summaries if s_id == selected_id), None)
                if selected_summary:
                    with st.expander("블로그 요약 전체 내용", expanded=True):
                        st.markdown(selected_summary)
            
            st.markdown("---")  # 구분선 추가
        else:
            st.info("저장된 블로그 요약이 없습니다.")
            
    except Exception as e:
        st.error(f"블로그 요약 데이터 조회 중 오류가 발생했습니다: {str(e)}")
        # 에러 발생 시 연결 닫기
        try:
            if 'conn' in locals() and conn:
                conn.rollback()
                cur.close()
                conn.close()
        except:
            pass

    load_keyword = st.text_input('키워드를 입력하세요. 해당 키워드로 그루핑된 블로그 내용들을 통합 분석합니다.')
    int_analyse_button = st.button("통합 분석 시작", type="primary")  # integrated analysis
    
    if load_keyword and int_analyse_button:
        with st.spinner(f"키워드 '{load_keyword}'로 저장된 블로그 요약을 통합 분석 중..."):
            try:
                # 데이터베이스에서 해당 키워드로 저장된 요약 조회
                conn = connect_postgres()
                cur = conn.cursor()
                
                cur.execute("""
                SELECT summary FROM blog_summary 
                WHERE keyword = %s
                """, (load_keyword,))
                
                summaries = [row[0] for row in cur.fetchall()]
                
                # 조회된 요약이 없는 경우
                if not summaries:
                    st.error(f"키워드 '{load_keyword}'로 저장된 블로그 요약이 없습니다.")
                else:
                    # 통합 분석을 위한 프롬프트 작성
                    prompt = f"""
다음은 "{load_keyword}" 키워드와 관련된 {len(summaries)}개의 블로그 포스트 요약입니다:

"""
                    for i, summary in enumerate(summaries):
                        prompt += f"\n--- 블로그 {i+1} ---\n{summary}\n"
                    
                    prompt += f"""
위의 블로그 요약들을 통합적으로 분석하여, 다음 사항을 포함한 포괄적인 요약을 작성해주세요:

1. 주요 내용 및 공통 주제
2. 중요한 사실이나 정보
3. 서로 다른 관점이나 의견 (있는 경우)
4. 관련 이벤트나 일정 (있는 경우)
5. 전체 내용을 종합한 통찰

"{load_keyword}" 키워드를 중심으로 이 모든 블로그 내용을 잘 통합해서 요약해주세요.
"""
                    
                    # OpenAI API를 통한 통합 분석
                    response = openai_client.chat.completions.create(
                        model=llm_option, 
                        messages=[
                            {"role": "system", "content": "당신은 여러 블로그 포스트의 요약을 통합하여 분석하는 전문가입니다."},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        max_tokens=1500
                    )
                    
                    integrated_summary = response.choices[0].message.content.strip()
                    
                    # 고유 ID 생성
                    search_id = search_unique_id()
                    
                    # 통합 분석 결과 저장
                    cur.execute("""
                    INSERT INTO blog_int_summary (search_unique_id, keyword, int_summary)
                    VALUES (%s, %s, %s)
                    """, (search_id, load_keyword, integrated_summary))
                    
                    conn.commit()
                    
                    # 성공 메시지 표시
                    st.success(f"{len(summaries)}개의 블로그 요약이 성공적으로 통합 분석되었습니다.")
                    
                    # 결과 표시
                    st.subheader("통합 분석 결과")
                    st.write(integrated_summary)
                
                cur.close()
                conn.close()
                
            except Exception as e:
                st.error(f"통합 분석 중 오류가 발생했습니다: {str(e)}")
                # 에러 발생 시 연결 닫기
                try:
                    if 'conn' in locals() and conn:
                        conn.rollback()
                        cur.close()
                        conn.close()
                except:
                    pass
        
# 탭 7: 콘텐츠 생성하기 탭
with tab7:
    st.subheader("콘텐츠 생성하기")
    
    # 세션 상태 초기화
    if 'content_generated_yt' not in st.session_state:
        st.session_state.content_generated_yt = False
    
    # 세션 상태 초기화
    if 'content_generated_ig' not in st.session_state:
        st.session_state.content_generated_ig = False
        
    # 세션 상태 초기화
    if 'content_generated_th' not in st.session_state:
        st.session_state.content_generated_th = False

    blog_id = st.text_input('주제로 삼을 블로그 요약본의 분석 아이디를 입력하세요.')
    
    # 블로그 키워드 리스트
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 모든 통합 블로그 분석 데이터 조회
        cur.execute("""SELECT search_unique_id, keyword FROM blog_int_summary""")
        
        blog_summaries = cur.fetchall()
        cur.close()
        conn.close()
        
        # 데이터 표시
        if blog_summaries:
            st.subheader("참고 가능한 블로그 통합 분석 내용")
            
            # 데이터프레임으로 변환
            df = pd.DataFrame(blog_summaries, columns=['분석ID', '키워드'])
            
            # 데이터프레임 표시
            st.dataframe(
                df,
                column_config={
                    "분석ID": st.column_config.Column(width="small"),
                    "키워드": st.column_config.Column(width="medium"),
                },
                hide_index=True,
                use_container_width=True
            )
            
            # 통합 분석 내용 보기 섹션
            selected_id = st.selectbox(
                "상세 내용을 확인할 블로그 분석 ID를 선택하세요:",
                options=[row[0] for row in blog_summaries],
                format_func=lambda x: f"ID: {x} - 키워드: {df[df['분석ID']==x]['키워드'].values[0]}"
            )
            
            if selected_id:
                # 선택한 ID의 통합 분석 내용 가져오기
                conn = connect_postgres()
                cur = conn.cursor()
                cur.execute("""SELECT int_summary FROM blog_int_summary WHERE search_unique_id = %s""", (selected_id,))
                
                summary_result = cur.fetchone()
                cur.close()
                conn.close()
                
                if summary_result:
                    with st.expander("블로그 통합 분석 내용", expanded=True):
                        st.markdown(summary_result[0])
        else:
            st.info("저장된 블로그 통합 분석 내용이 없습니다.")
    
    except Exception as e:
        st.error(f"블로그 통합 분석 데이터 조회 중 오류가 발생했습니다: {str(e)}")
    
    st.markdown("---")
    
    # 콘텐츠 생성 버튼
    yt_button = st.button("유튜브 콘텐츠 만들기", type="primary")
    insta_button = st.button("인스타 콘텐츠 만들기", type="primary")
    thrd_button = st.button("쓰레드 콘텐츠 만들기", type="primary")
    
    # 유튜브 콘텐츠 생성
    if yt_button and blog_id:
        with st.spinner('유튜브 콘텐츠를 생성하는 중입니다...'):
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                
                # 블로그 통합 요약 내용 불러오기
                cur.execute("""SELECT int_summary, keyword FROM blog_int_summary WHERE search_unique_id = %s""", (blog_id,))
                result = cur.fetchone()

                # 긍정적인 평가 내용 불러오기
                cur.execute("""
            SELECT score, feedback, title, thumbnail, script
            FROM feedback
            WHERE platform = 'YouTube' AND score >= 7
            ORDER BY score DESC
            LIMIT 3
            """)
                high_feedback = cur.fetchall()

                # 부정적인 평가 내용 불러오기
                cur.execute("""
            SELECT score, feedback, title, thumbnail, script
            FROM feedback
            WHERE platform = 'YouTube' AND score <= 4
            ORDER BY score ASC
            LIMIT 3
            """)
                low_feedback = cur.fetchall()
                
                cur.close()
                conn.close()
                    
                if result:
                    blog_summary = result[0]
                    keyword = result[1]
                else:
                    st.error(f"ID {blog_id}에 해당하는 블로그 분석을 찾을 수 없습니다.")
                    st.stop()

                # 피드백 데이터 요약 및 통합
                high_feedback_summary = "좋은 평가를 받은 콘텐츠의 특징:\n"
                if high_feedback:
                    # 높은 점수 피드백의 주요 내용 통합
                    for i, (score, feedback, title, _, _) in enumerate(high_feedback):
                        high_feedback_summary += f"{i+1}. 점수 {score}/10: {feedback}\n"
                        
                    # 좋은 예시 제목 추가
                    high_feedback_summary += "\n좋은 평가를 받은 제목 예시:\n"
                    for i, (_, _, title, _, _) in enumerate(high_feedback[:3]):  # 상위 3개만
                        high_feedback_summary += f"- {title}\n"
                else:
                    high_feedback_summary += "아직 충분한 데이터가 없습니다.\n"
                
                low_feedback_summary = "개선이 필요한 콘텐츠의 특징 (피해야 할 점):\n"
                if low_feedback:
                    # 낮은 점수 피드백의 주요 내용 통합
                    for i, (score, feedback, _, _, _) in enumerate(low_feedback):
                        low_feedback_summary += f"{i+1}. 점수 {score}/10: {feedback}\n"
                else:
                    low_feedback_summary += "아직 충분한 데이터가 없습니다.\n"

                prompt = f"""
새로 만들 유튜브 동영상을 위한 제목, 썸네일 이미지 내용, 첫 2분 스크립트 내용을 생성해야 합니다.

제목 및 썸네일 이미지 내용은 다음 카피라이팅 법칙 5가지를 꼭 지켜서 생성해주세요:
1. NUMBERS (구체적 숫자 / 전후 비교)
2. ONE & ONLY (하나만 지키면 된다 / 단 하나의 비밀)
3. SHOCK & HOOK (충격·호기심 + 짧고 강렬한 표현)
4. AUTHORITY (전문가·유명인 인용, 권위 부여)
5. URGENCY (시급성·즉시성)

첫 2분 스크립트는 다음 글쓰기 법칙 4가지를 꼭 지켜서 작성해주세요:
1. SHORT & SIMPLE (짧고 간결하게, 핵심 먼저)
2. HOOK & FLOW (후킹 → 자연스러운 흐름)
3. YOU-FOCUSED (독자 중심, 독자의 이익 강조)
4. CREDIBILITY & ACTION (신뢰도 확보 + 행동 유도)

블로그 통합 요약 내용: {blog_summary}

사용자 피드백 분석:
{high_feedback_summary}

{low_feedback_summary}

위 정보들을 토대로 '{keyword}'에 관한 동영상 제목 및 썸네일 이미지 내용 각각 3가지, 스크립트 하나를 생성해주세요.

꼭 아래 포맷대로 생성 해주세요:
[제목]
제목 3가지

[썸네일]
썸네일 3가지

[스크립트]
첫 2분 스크립트 내용
"""
                content = openai_client.chat.completions.create(
                    model=llm_option, 
                    messages=[
                        {"role": "system", "content": "당신은 카피라이팅 법칙을 따라 수집된 데이터에 기반하여 유튜브 동영상 컨텐츠를 만드는 전문가입니다."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3, 
                    max_tokens=1500, 
                    # n=3, 
                )
                
                result_yt = content.choices[0].message.content.strip()
                
                # 세션 상태에 결과 저장 (화면 표시용)
                st.session_state.generated_content_yt = result_yt
                st.session_state.content_generated_yt = True

                try:
                    print("유튜브 콘텐츠 프롬프트 내용\n\n", prompt)
                    parts = result_yt.split("\n\n")
                    
                    title = ""
                    thumbnail = ""
                    script = ""
                        
                    # 각 부분 추출
                    for i, part in enumerate(parts):
                        if part.startswith("[제목]"):  # 제목 문자열 추출 (첫 줄은 [제목]이므로 제외)
                            title_lines = part.split("\n")[1:]
                            title = "\n".join(title_lines).strip()
                        elif part.startswith("[썸네일]"):  # 썸네일 문자열 추출 (첫 줄은 [썸네일]이므로 제외)
                            thumbnail_lines = part.split("\n")[1:]
                            thumbnail = "\n".join(thumbnail_lines).strip()
                        elif part.startswith("[스크립트]"):  # 스크립트 문자열 추출 (첫 줄은 [스크립트]이므로 제외)
                            script_lines = part.split("\n")[1:]
                            script = "\n".join(script_lines).strip()
                    
                    # 생성한 유튜브 컨텐츠 정보를 DB에 저장
                    try:
                        conn = connect_postgres()
                        cur = conn.cursor()
                            
                        # 생성된 콘텐츠 저장
                        cur.execute("""
                            INSERT INTO youtube_content (blog_id, keyword, title, thumbnail, script)
                            VALUES (%s, %s, %s, %s, %s)
                            """, (blog_id, keyword, title, thumbnail, script))
                        
                        conn.commit()
                        cur.close()
                        conn.close()
                        
                        st.success("유튜브 콘텐츠가 성공적으로 생성되고 저장되었습니다!")

                    except Exception as e:
                        st.error(f"유튜브 콘텐츠 저장 중 오류가 발생했습니다: {str(e)}")
                
                except Exception as e:
                    st.error(f"유튜브 생성된 콘텐츠 파싱 중 오류가 발생했습니다: {str(e)}")
            
            except Exception as e:
                st.error(f"유튜브 콘텐츠 생성 중 오류가 발생했습니다: {str(e)}")
    elif yt_button and not blog_id:
        st.warning("블로그 분석 ID를 입력해주세요.")
    
#     # 인스타 콘텐츠 생성 (보류)
#     if insta_button and blog_id:
#         with st.spinner('인스타그램 콘텐츠를 생성하는 중입니다...'):
#             try:
#                 conn = connect_postgres()
#                 cur = conn.cursor()
#                 cur.execute("""SELECT int_summary, keyword FROM blog_int_summary WHERE search_unique_id = %s""", (blog_id,))
                
#                 result = cur.fetchone()
#                 cur.close()
#                 conn.close()
                    
#                 if result:
#                     blog_summary = result[0]
#                     keyword = result[1]
#                 else:
#                     st.error(f"ID {blog_id}에 해당하는 블로그 분석을 찾을 수 없습니다.")
#                     st.stop()


#                 prompt = f"""
# 위 정보들을 토대로 '{keyword}'에 관한 동영상 제목 및 썸네일 이미지 내용 각각 3가지, 스크립트 하나를 생성해주세요.

# 꼭 아래 포맷대로 생성 해주세요:
# [제목]
# 제목 3가지

# [썸네일]
# 썸네일 3가지

# [스크립트]
# 스크립트 1가지
# """
#                 content_ig = openai_client.chat.completions.create(
#                     model=llm_option, 
#                     messages=[
#                         {"role": "system", "content": "당신은 카피라이팅 법칙을 따라 수집된 데이터에 기반하여 인스타그램 컨텐츠를 만드는 전문가입니다."},
#                         {"role": "user", "content": prompt}
#                     ],
#                     temperature=0.3,
#                     max_tokens=1500
#                 )
                
#                 result_ig = content_ig.choices[0].message.content.strip()
                
#                 # 세션 상태에 결과 저장 (화면 표시용)
#                 st.session_state.generated_content_ig = result_ig
#                 st.session_state.content_generated_ig = True

#                 try:
#                     parts = result_ig.split("\n\n")
                    
#                     # title = ""
#                     # thumbnail = ""
#                     # script = ""
                        
#                     # # 각 부분 추출
#                     # for i, part in enumerate(parts):
#                     #     if part.startswith("[제목]"):  # 제목 문자열 추출 (첫 줄은 [제목]이므로 제외)
#                     #         title_lines = part.split("\n")[1:]
#                     #         title = "\n".join(title_lines).strip()
#                     #     elif part.startswith("[썸네일]"):  # 썸네일 문자열 추출 (첫 줄은 [썸네일]이므로 제외)
#                     #         thumbnail_lines = part.split("\n")[1:]
#                     #         thumbnail = "\n".join(thumbnail_lines).strip()
#                     #     elif part.startswith("[스크립트]"):  # 스크립트 문자열 추출 (첫 줄은 [스크립트]이므로 제외)
#                     #         script_lines = part.split("\n")[1:]
#                     #         script = "\n".join(script_lines).strip()
                    
#                     # 생성한 인스타그램램 컨텐츠 정보를 DB에 저장
#                     try:
#                         conn = connect_postgres()
#                         cur = conn.cursor()
                            
#                         # 생성된 콘텐츠 저장
#                         cur.execute("""
#                             INSERT INTO instagram_content (blog_id, keyword, title, thumbnail, script)
#                             VALUES (%s, %s, %s, %s, %s)
#                             """, (blog_id, keyword, title, thumbnail, script))
                        
#                         conn.commit()
#                         cur.close()
#                         conn.close()
                        
#                         st.success("인스타그램 콘텐츠가 성공적으로 생성되고 저장되었습니다!")

#                     except Exception as e:
#                         st.error(f"인스타그램 콘텐츠 저장 중 오류가 발생했습니다: {str(e)}")
                
#                 except Exception as e:
#                     st.error(f"생성된 인스타그램 콘텐츠 파싱 중 오류가 발생했습니다: {str(e)}")
            
#             except Exception as e:
#                 st.error(f"인스타그램 콘텐츠 생성 중 오류가 발생했습니다: {str(e)}")
#     elif insta_button and not blog_id:
#         st.warning("블로그 분석 ID를 입력해주세요.")
        
    # 쓰레드 콘텐츠 생성
    if thrd_button and blog_id:
        with st.spinner('쓰레드 콘텐츠를 생성하는 중입니다...'):
            try:
                conn = connect_postgres()
                cur = conn.cursor()
                cur.execute("""SELECT int_summary, keyword FROM blog_int_summary WHERE search_unique_id = %s""", (blog_id,))
                
                result = cur.fetchone()
                cur.close()
                conn.close()
                    
                if result:
                    blog_summary = result[0]
                    keyword = result[1]
                else:
                    st.error(f"ID {blog_id}에 해당하는 블로그 분석을 찾을 수 없습니다.")
                    st.stop()

                prompt = f"""

또한 이 블로그 통합 요약 내용도 반영해주세요: {blog_summary}

위 정보들을 토대로 '{keyword}'에 관한 동영상 제목 및 썸네일 이미지 내용 각각 3가지, 스크립트 하나를 생성해주세요.

꼭 아래 포맷대로 생성 해주세요:
[제목]
제목 3가지

[썸네일]
썸네일 3가지

[스크립트]
스크립트 1가지
"""
                
                content_th = openai_client.chat.completions.create(
                    model=llm_option, 
                    messages=[
                        {"role": "system", "content": "당신은 카피라이팅 법칙을 따라 수집된 데이터에 기반하여 쓰레드 컨텐츠를 만드는 전문가입니다."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=1500
                )
                
                result_th = content.choices[0].message.content.strip()
                
                # 세션 상태에 결과 저장 (화면 표시용)
                st.session_state.generated_content_th = result_th
                st.session_state.content_generated_th = True

                try:
                    parts = result_th.split("\n\n")
                    
                    # title = ""
                    # thumbnail = ""
                    # script = ""
                        
                    # # 각 부분 추출
                    # for i, part in enumerate(parts):
                    #     if part.startswith("[제목]"):  # 제목 문자열 추출 (첫 줄은 [제목]이므로 제외)
                    #         title_lines = part.split("\n")[1:]
                    #         title = "\n".join(title_lines).strip()
                    #     elif part.startswith("[썸네일]"):  # 썸네일 문자열 추출 (첫 줄은 [썸네일]이므로 제외)
                    #         thumbnail_lines = part.split("\n")[1:]
                    #         thumbnail = "\n".join(thumbnail_lines).strip()
                    #     elif part.startswith("[스크립트]"):  # 스크립트 문자열 추출 (첫 줄은 [스크립트]이므로 제외)
                    #         script_lines = part.split("\n")[1:]
                    #         script = "\n".join(script_lines).strip()
                    
                    # 생성한 쓰레드 컨텐츠 정보를 DB에 저장
                    try:
                        conn = connect_postgres()
                        cur = conn.cursor()
                            
                        # 생성된 콘텐츠 저장
                        cur.execute("""
                            INSERT INTO thread_content (blog_id, keyword, title, thumbnail, script)
                            VALUES (%s, %s, %s, %s, %s)
                            """, (blog_id, keyword, title, thumbnail, script))
                        
                        conn.commit()
                        cur.close()
                        conn.close()
                        
                        st.success("콘텐츠가 성공적으로 생성되고 저장되었습니다!")

                    except Exception as e:
                        st.error(f"콘텐츠 저장 중 오류가 발생했습니다: {str(e)}")
                
                except Exception as e:
                    st.error(f"생성된 콘텐츠 파싱 중 오류가 발생했습니다: {str(e)}")
            except Exception as e:
                st.error(f"콘텐츠 생성 중 오류가 발생했습니다: {str(e)}")
    elif thrd_button and not blog_id:
        st.warning("블로그 분석 ID를 입력해주세요.")
    
    
    # 생성된 유튜브 콘텐츠 표시
    if st.session_state.get('content_generated_yt', False):
        st.subheader("생성된 유튜브 콘텐츠")
        
        # 원본 텍스트 (접을 수 있게)
        with st.expander("원본 생성 텍스트 보기", expanded=True):
            if hasattr(st.session_state, 'generated_content_yt'):
                st.text(st.session_state.generated_content_yt)
    
    # 생성된 인스타그램 콘텐츠 표시
    if st.session_state.get('content_generated_ig', False):
        st.subheader("생성된 인스타그램 콘텐츠")
        
        # 원본 텍스트 (접을 수 있게)
        with st.expander("원본 생성 텍스트 보기", expanded=True):
            if hasattr(st.session_state, 'generated_content_ig'):
                st.text(st.session_state.generated_content_ig)
    
    # 생성된 쓰레드 콘텐츠 표시
    if st.session_state.get('content_generated_th', False):
        st.subheader("생성된 쓰레드 콘텐츠")
        
        # 원본 텍스트 (접을 수 있게)
        with st.expander("원본 생성 텍스트 보기", expanded=True):
            if hasattr(st.session_state, 'generated_content_th'):
                st.text(st.session_state.generated_content_th)

# 탭 8: 콘텐츠 평가하기 탭
with tab8:
    st.subheader("콘텐츠 평가하기")
    
    # 유튜브 콘텐츠 평가
    st.subheader("유튜브 콘텐츠 품질 평가")
    
    try:
        conn = connect_postgres()
        cur = conn.cursor()
        
        # 모든 유튜브 콘텐츠 조회
        cur.execute("""
        SELECT id, title, thumbnail, script 
        FROM youtube_content 
        ORDER BY created_at DESC
        """)
        
        youtube_contents = cur.fetchall()
        cur.close()
        conn.close()
        
        if youtube_contents:
            df_contents = pd.DataFrame(youtube_contents, columns=['ID', '제목', '썸네일', '스크립트'])
            st.dataframe(
                df_contents,
                column_config={
                    "ID": st.column_config.Column(width="small"),
                    "제목": st.column_config.Column(width="large"),
                    "썸네일": st.column_config.Column(width="large"),
                    "스크립트": st.column_config.Column(width="large"),
                },
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("평가할 유튜브 콘텐츠가 없습니다. 먼저 콘텐츠를 생성해주세요.")
            
    except Exception as e:
        st.error(f"콘텐츠 목록을 불러오는 중 오류가 발생했습니다: {str(e)}")
    
    content_id = st.number_input("평가할 콘텐츠 ID를 입력하세요", min_value=1, step=1)
    feedback_score_yt = st.slider("이 콘텐츠의 품질을 평가해주세요", 1, 10, 7)
    feedback_text_yt = st.text_area("개선 사항이나 추가 의견이 있다면 알려주세요")
    submit_feedback_yt = st.button("피드백 제출", key="submit_youtube")

    if submit_feedback_yt and feedback_score_yt and feedback_text_yt and content_id:
        # 데이터베이스에서 해당 ID의 콘텐츠 정보 가져오기
        try:
            conn = connect_postgres()
            cur = conn.cursor()
            
            cur.execute("""
            SELECT title, thumbnail, script
            FROM youtube_content
            WHERE id = %s
            """, (content_id,))
            
            content_info = cur.fetchone()
            cur.close()
            conn.close()
            
            if content_info:
                title, thumbnail, script = content_info
                
                # 피드백 저장
                success = save_feedback(content_id, title, thumbnail, script, feedback_score_yt, feedback_text_yt, "YouTube")

                if success:
                    st.success(f"콘텐츠 ID {content_id}에 대한 피드백이 성공적으로 저장되었습니다!")
            else:
                st.error(f"ID {content_id}에 해당하는 콘텐츠를 찾을 수 없습니다.")
                
        except Exception as e:
            st.error(f"콘텐츠 정보를 가져오는 중 오류가 발생했습니다: {str(e)}")
    elif submit_feedback_yt:
        if not content_id:
            st.warning("평가할 콘텐츠 ID를 입력해주세요.")
        elif not feedback_text_yt:
            st.warning("개선 사항이나 의견을 입력해주세요.")

        
    # # 인스타그램 콘텐츠 평가
    # st.subheader("인스타그램 콘텐츠 품질 평가")
    # feedback_score_ig = st.slider("이 인스타그램 콘텐츠의 품질을 평가해주세요", 1, 10, 7)
    # feedback_text_ig = st.text_area("개선 사항이나 추가 의견이 있다면 알려주세요")
    # submit_feedback_ig = st.button("피드백 제출", key="submit_instagram")

    # if submit_feedback_ig:
    #     if hasattr(st.session_state, 'title_ig') and hasattr(st.session_state, 'thumbnail_ig') and hasattr(st.session_state, 'script_ig'):
    #         success = save_feedback(
    #             st.session_state.title_ig, st.session_state.thumbnail_ig, st.session_state.script_ig, 
    #             feedback_score_yt, feedback_text_yt, "Instagram"
    #         )

    #         if success:
    #             st.success("인스타그램 콘텐츠에 대한 피드백이 성공적으로 저장되었습니다!")
    # else:
    #     st.error("피드백을 저장할 콘텐츠 정보가 없습니다.")
    

    # # 쓰레드 콘텐츠 평가
    # st.subheader("쓰레드 콘텐츠 품질 평가")
    # feedback_score_th = st.slider("이 쓰레드 콘텐츠의 품질을 평가해주세요", 1, 10, 7)
    # feedback_text_th = st.text_area("개선 사항이나 추가 의견이 있다면 알려주세요")
    # submit_feedback_th = st.button("피드백 제출", key="submit_threads")

    # if submit_feedback_th:
    #     if hasattr(st.session_state, 'title_th') and hasattr(st.session_state, 'thumbnail_th') and hasattr(st.session_state, 'script_th'):
    #         success = save_feedback(
    #             st.session_state.title_th, st.session_state.thumbnail_th, st.session_state.script_th, 
    #             feedback_score_th, feedback_text_th, "Threads"
    #         )

    #         if success:
    #             st.success("스레드 콘텐츠에 대한 피드백이 성공적으로 저장되었습니다!")
    # else:
    #     st.error("피드백을 저장할 콘텐츠 정보가 없습니다.")

# 탭 9: 분석 내용 확인하기 탭
with tab9:
    st.subheader("썸네일 분석 내용 정리")
    
    try:
        conn = connect_postgres()
        cur = conn.cursor()

        cur.execute("""
        SELECT 
            video_thumbnail, search_unique_id, keyword, channel_url, channel_name, video_id, video_title, is_shorts, thumbnail_analysis
        FROM 
            thumbnail_analysis
        ORDER BY
            created_at DESC
        """)

        results = cur.fetchall()
        
        cur.close()
        conn.close()

        # 키워드도 조회해야
        columns = ['썸네일', '검색ID', '키워드', '채널URL', '채널명', 'video_id', '제목', '쇼츠', '썸네일 분석']
        
        df = pd.DataFrame(results, columns=columns)
        st.dataframe(
            df,
            column_config={
                "썸네일": st.column_config.ImageColumn(width="medium"),
                "채널명": st.column_config.Column(width="small"),
                "제목": st.column_config.Column(width="medium"),
                "썸네일 분석": st.column_config.TextColumn(width="large"),
                "생성일시": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
            },
            hide_index=True,
            use_container_width=True
        )
    
    except Exception as e:
        st.error(f"데이터 조회 중 오류가 발생했습니다: {str(e)}")
