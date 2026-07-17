# AI / 缁存姢鑰呬氦鎺ワ細Git 涓庝粨搴撹鏄?
> 缁欎笅涓€涓?AI 鎴栦汉绫荤淮鎶よ€咃細鍙鏈枃浠跺嵆鍙帴涓婂綋鍓?Git 鐘舵€併€? 
> **绾︽潫锛堢敤鎴锋槑纭姹傦級锛氫笉瑕佷慨鏀逛富瑕侀」鐩笟鍔℃枃浠?*锛坄main.py`銆乣rm_client/**` 閫昏緫绛夛級锛岄櫎闈炵敤鎴峰彟璇淬€?
---

## 1. 浠撳簱韬唤

| 椤?| 鍊?|
|----|-----|
| 鏈湴璺緞 | `C:\Users\lulu\Desktop\claude\openzenuicodexver\rm_custom_client` |
| GitHub | https://github.com/lulunomaro/polar-rm-client |
| 杩滅▼鍚?| `origin` |
| 榛樿鍒嗘敮 | `main` |
| 椤圭洰鍐呭 | POLAR-RM 鑷畾涔夊鎴风锛圥ython + PySide6锛?|

**娉ㄦ剰锛?* 鐖剁洰褰?`openzenuicodexver\` 鏄祫鏂欏爢锛堝紑婧愬弬鑰冦€丳DF銆亃ip銆佽棰戯級銆? 
**Git 浠撳簱鏍瑰彧鍦?`rm_custom_client/`锛屼笉瑕佸鏁翠釜鐖剁洰褰曞仛 `git init`銆?*

鐖剁洰褰曞彲鑳藉瓨鍦?*绌虹殑/鏃犳晥** `openzenuicodexver\.git`锛堟棤 HEAD锛夈€傚拷鐣ュ畠锛涙湁鏁堜粨搴撴槸鏈洰褰曚笅鐨?`.git`銆?
---

## 2. 鏈锛?026-07-17锛夊凡瀹屾垚鐨勬搷浣?
1. 鏂板 `.gitignore`锛堝拷鐣?`.venv`銆佺紦瀛樸€乼mp 鎴浘銆乺elease zip銆佸ぇ瑙嗛澹佺焊銆佺敤鎴峰绾搞€佹牎鍑嗘暟鎹瓑锛夈€?2. 鏂板 `calibrations/.gitkeep`锛堜繚鐣欑┖鐩綍锛夈€?3. 鏂板鏈枃浠?`AI_GIT_HANDOFF.md`銆?4. 鍦?`rm_custom_client` 鎵ц `git init -b main`锛岄娆℃彁浜ゆ闈富绾匡紙286 鏂囦欢锛夆啋 commit `fd06f52`銆?5. 娣诲姞 remote `origin` 鈫?`https://github.com/lulunomaro/polar-rm-client.git`銆?6. 杩滅▼鍘熷厛浠呮湁 GitHub 鍒濆鍖栫殑 `README.md`锛坄1864da2`锛夈€傚凡 `git pull --allow-unrelated-histories`锛汻EADME 鍐茬獊鏃?**淇濈暀鏈湴椤圭洰 README**锛坄git checkout --ours`锛夛紝鍚堝苟鎻愪氦 `182f225`銆?7. 宸叉垚鍔?`git push -u origin main`锛涙湰鍦颁笌 `origin/main` 鍚屾锛圚EAD = `182f225`锛夈€?8. **鏈慨鏀?*浠讳綍涓氬姟婧愮爜閫昏緫锛坄main.py`銆乣rm_client/*.py` 绛夋寜鍘熸牱鍏ュ簱锛屾湭鍋氬姛鑳芥敼鍔級銆?
### 鏁呴殰鎺掓煡锛坧ush 琚嫆 / 璁よ瘉锛?
- `rejected (fetch first)`锛氳繙绋嬫湁鎻愪氦鏃跺厛 `git pull origin main --allow-unrelated-histories`锛岃В鍐冲啿绐佸悗鍐?push銆?- 璁よ瘉寮圭獥锛歐indows Credential Manager锛涙垨鍒涘缓 GitHub PAT 鍚庣敤 HTTPS 鎺ㄩ€併€?- 鏈満鏇炬棤 `gh` CLI / 鏃?SSH 绉侀挜锛屽綋鍓嶆垚鍔熻矾寰勬槸 HTTPS銆?
---

## 3. 鍒绘剰鏈撼鍏?Git 鐨勫唴瀹?
| 璺緞/绫诲瀷 | 鍘熷洜 |
|-----------|------|
| `.venv/` | ~1GB 铏氭嫙鐜锛屾湰鏈洪噸寤?|
| `tmp_render_checks/`銆乣tmp_*` | 璋冭瘯鎴浘 |
| `.wallpaper_cache/` | 杩愯缂撳瓨 |
| `release/*.zip` | 鍙戝竷鍖呬綋绉ぇ |
| `assets/wallpapers/**/*.mp4` 绛?| 澶у獟浣擄紱鏈満淇濈暀 |
| `assets/wallpapers/user/` | 鐢ㄦ埛瀵煎叆 |
| `calibrations/*`锛堥櫎 `.gitkeep`锛?| 鏈哄櫒鐩稿叧 |
| `_backup_*` | 鏈湴澶囦唤 |
| 鐖剁洰褰曞紑婧愭暣浠?/ PDF / zip | 涓嶅睘浜庢湰浠撳簱 |

鍏嬮殕鍚庨渶瑕侊細

```powershell
cd rm_custom_client   # 鎴?clone 涓嬫潵鐨勭洰褰曞悕
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
# 澹佺焊瑙嗛鑻ラ渶瑕侊紝浠庡師鏈?assets/wallpapers 鎷峰洖
```

---

## 4. 骞宠宸ヤ綔绾匡紙灏氭湭瀵煎叆鏈粨搴擄級

鐢ㄦ埛鍙︽湁涓€浠?*鏂囦欢澶规嫹璐?*褰撲綔 Gestalt 瀹為獙瀹ゅ垎鏀細

- 璺緞锛歚G:\SteamLibrary\steamapps\common\GestaltSystem\RMaccseeplan\rm_custom_client`
- 鍚?`gestalt_*`銆乣input_bridge`銆乣start_lab_launcher.bat`銆乣workshop_telemetry_relay` 绛?- 涓庢闈?`main` **宸插垎鍙?*锛堜簰鏈夌嫭鏈夋枃浠讹級锛?*灏氭湭**浣滀负 `gestalt-lab` 鍒嗘敮瀵煎叆

**涓嬩竴姝ワ紙浠呭綋鐢ㄦ埛瑕佹眰鏃讹級锛?*

```powershell
cd C:\Users\lulu\Desktop\claude\openzenuicodexver\rm_custom_client
git checkout main
git checkout --orphan gestalt-lab
git rm -rf .   # 娓呯┖绱㈠紩锛涚‘璁?cwd 姝ｇ‘
robocopy "G:\SteamLibrary\steamapps\common\GestaltSystem\RMaccseeplan\rm_custom_client" . /E /XD .venv __pycache__ .git .wallpaper_cache tmp_render_checks tmp_selfcheck
# 淇濈暀鏈粨搴撶殑 .gitignore 涓?AI_GIT_HANDOFF.md锛堣嫢琚鐩栧垯浠?main 鍙栧洖锛?git checkout main -- .gitignore AI_GIT_HANDOFF.md
git add .
git commit -m "Import gestalt-lab snapshot from RMaccseeplan"
git push -u origin gestalt-lab
git checkout main
```

**涓嶈**鐢ㄦ暣澶瑰鍒惰鐩栧凡璺熻釜鐨?Git 宸ヤ綔鍖烘潵銆屽悓姝ャ€嶃€?
---

## 5. 鏈満 Git 璐﹀彿鐘舵€侊紙涓婃妫€鏌ワ級

- 鍏ㄥ眬 `user.name` = `lulunomaro`
- 鍏ㄥ眬 `user.email` = `mhxiaoyao233@gmail.com`
- `credential.helper` = `manager`锛圵indows锛?- **鏃?* `gh` CLI锛?*鏃?* SSH 绉侀挜锛堜粎 `known_hosts`锛?- 鎺ㄩ€佷緷璧?HTTPS + Git Credential Manager / PAT

鎻愪氦缃插悕宸查厤缃紱鑳藉惁 push 鍙栧喅浜?GitHub 鏄惁宸插湪鏈満鐧诲綍銆?
---

## 6. 鏃ュ父鍛戒护锛堢粰浜虹被 / AI锛?
```powershell
cd C:\Users\lulu\Desktop\claude\openzenuicodexver\rm_custom_client
git status
git add <paths>
git commit -m "绠€鐭鏄?
git push
git pull
```

鎹㈠垎鏀墠鍏?commit 鎴?`git stash`銆?
---

## 7. 寮€婧愪笌鐖剁洰褰曡祫鏂?
鐖剁洰褰曞弬鑰冮」鐩紙**涓嶈**濉炶繘鏈?repo锛夛細OpenZen銆乀est-Client銆丷eGlass銆丄ndroidLiquidGlass-kmp銆丩iquidBounce銆丏ynamicIsland銆乬d656killicon銆丼harkDataSever 绛夈€? 
涓氬姟渚ц鏄庤 `THIRD_PARTY_NOTICES.md`銆侺iquidBounce 鐩稿叧绱犳潗娉ㄦ剰 GPL 鍚堣锛涘叕寮€鍒嗗彂鍓嶅啀瀹°€?
---

## 8. 纭€х害鏉燂紙鐢ㄦ埛锛?
1. 涓嶈鏀逛富瑕侀」鐩笟鍔℃枃浠讹紙闄ら潪鐢ㄦ埛鏄庣‘瑕佹眰鍔熻兘寮€鍙戯級銆?2. 涓嶈鎶婃暣涓?`openzenuicodexver` 寤烘垚涓€涓?monorepo銆?3. 涓嶈鍒犻櫎 G 鐩?Gestalt 鐩綍鎴栨闈㈢洰褰曘€屼负浜嗗共鍑€銆嶃€?4. 鏈夌牬鍧忔€ф搷浣滐紙force push銆佸垹鍒嗘敮銆佹敼鍘嗗彶锛夊墠鍏堥棶鐢ㄦ埛銆?
---

## 9. 鍙樻洿鏃ュ織锛堣拷鍔狅級

| 鏃ユ湡 | 鎿嶄綔 | 鎿嶄綔鑰?|
|------|------|--------|
| 2026-07-17 | 鍒濆鍖栦粨搴撱€乣.gitignore`銆佸悎骞惰繙绋嬬┖ README銆乸ush `main`锛坄182f225`锛夈€佹湰浜ゆ帴鏂囦欢 | Grok锛堟湰浼氳瘽锛?|
