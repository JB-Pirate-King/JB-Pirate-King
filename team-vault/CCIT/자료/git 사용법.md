---
notion_url: https://www.notion.so/333be0809830800097d4c88ea8c3b404
last_synced: 2026-06-12 13:48
tags: [notion-sync]
---

# git 사용법

[https://inpa.tistory.com/entry/GIT-%E2%9A%A1%EF%B8%8F-%EA%B0%9C%EB%85%90-%EC%9B%90%EB%A6%AC-%EC%89%BD%EA%B2%8C%EC%9D%B4%ED%95%B4](https://inpa.tistory.com/entry/GIT-%E2%9A%A1%EF%B8%8F-%EA%B0%9C%EB%85%90-%EC%9B%90%EB%A6%AC-%EC%89%BD%EA%B2%8C%EC%9D%B4%ED%95%B4)

![image](_assets/image.png)

git clone

```c
원격 레포 로컬에 가져오기
git clone <remote repo>
```


git add

```c
변경사항을 스테이징 영역에 추가
git add <파일 또는 경로>
```


git commit 

```c
스테이징 영역에 있는 파일 변경 사항의 스냅샷을 로컬 저장소에 영구적으로 적용
git commit -m "커밋 로그 메세지"
```


git push

```c
커밋한 사항을 원격 레포에 업로드
git push
```


git pull

```c
원격 레포의 최신 변경 사항을 가져오기
git pull
```


git branch

```c
브랜치 생성 및 이동
git switch -c <만들 브랜치>

브랜치 이동
git switch <브랜치>
```


git merge

```c
main 브랜치에 dev 브랜치를 병합하는 방법
git swtich main
git merge dev
git push

병합은 깃허브 페이지에서 해도 됨 (풀 리퀘스트)
```


pull request : 본인이 수정한걸 병합하자고 제안하는거

워크플로우
[https://wayhome25.github.io/git/2017/07/08/git-first-pull-request-story/](https://wayhome25.github.io/git/2017/07/08/git-first-pull-request-story/)

```c
1. 포크
2. git clone <포크 된거 레포>
3. git remote add project https://github.com/JB-Pirate-King/JB-Pirate-King
                     git remote -v 로 확인
4. git switch -c develop
                     변경된 브랜치는 git branch -v 로 확인
5. 코드 수정~~
6. git add <수정한 파일 및 폴더>
                      변경된 파일은 git status로 확인
7. git push origin develop
                      이제 로컬 레포에 적용한걸 원격 레포에 적용하려면
8. 깃허브 본인 포크 레포 들어가서 풀리퀘 하기       
                      풀리퀘해서 메인 레포에 병합 되었으면
9. git switch main
10. git pull origin main
10. git branch -D develop
  이제 4부터 반복          
```
