import requests
from bs4 import BeautifulSoup
import re

def blog_content(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # iframe 찾기 (네이버 블로그는 iframe 내에 실제 콘텐츠가 있음)
        if 'blog.naver.com' in url:
            iframe_url = None
            
            # 메인 프레임 찾기
            frame_tag = soup.select_one('iframe#mainFrame')
            if frame_tag and 'src' in frame_tag.attrs:
                relative_url = frame_tag['src']
                iframe_url = f"https://blog.naver.com{relative_url}"
            
            if iframe_url:
                response = requests.get(iframe_url, headers=headers)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
        
        # 블로그 제목 추출
        title = soup.select_one('div.se-module-text h3.se-title-text') or \
                soup.select_one('div.se-title-text') or \
                soup.select_one('h3.se-title') or \
                soup.select_one('h3.title') or \
                soup.select_one('div.title h3') or \
                soup.select_one('title')
        
        if title:
            title = title.get_text(strip=True)
        else:
            title = "제목을 찾을 수 없습니다."
        
        # 본문 내용 추출
        content_elements = soup.select('div.se-main-container div.se-text p') or \
                           soup.select('div.se-main-container div.se-module-text') or \
                           soup.select('div.post-view') or \
                           soup.select('div.post_content') or \
                           soup.select('div#content div.story')
        
        content = ""
        for element in content_elements:
            content += element.get_text(strip=True) + "\n"
        
        # 내용이 없을 경우 다른 방법으로 시도
        if not content:
            paragraphs = soup.select('p') or soup.select('div.paragraph')
            for p in paragraphs:
                if p.get_text(strip=True):
                    content += p.get_text(strip=True) + "\n"
        
        # HTML 태그 및 특수 문자 제거
        content = re.sub(r'<[^>]+>', '', content)
        content = re.sub(r'\s+', ' ', content).strip()
        
        return {"title": title, "content": content}
    
    except Exception as e:
        print(f"오류 발생: {e}")
        return {"title": "오류 발생", "content": f"콘텐츠를 추출하는 도중 오류가 발생했습니다: {str(e)}"}
