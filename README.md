# PC Guard v2

Android phone se Windows laptop: Lock / Unlock / Shutdown, **live screen**, **mouse + keyboard control**, aur **laptop ki volume** (Android app mein phone ke volume buttons se bhi).

    server/    relay server + phone page (Node.js)
    agent/     Windows program (Python -> PCGuard.exe)
    android/   Android app (-> app-debug.apk)
    tests/     end-to-end tests (server + agent + phone ka code)
    .github/   exe + apk ka automatic build

## 1. exe aur apk banana (GitHub Actions)
1. Repo ki purani files hata kar is zip ka sara content upload karein (`.github` folder samet).
2. Actions tab > "Build exe and apk" > Run workflow.
3. Artifacts se `PCGuard.exe` aur `app-debug.apk` download karein (zip extract karein).

## 2. Server chalana (laptop par + Tailscale Funnel)
    cd server
    npm install
    node server.js

Phir ek doosri window mein: `tailscale funnel 8080` (ya `tailscale funnel --bg 8080`). Jo `https://...ts.net` link mile wohi server URL hai.

## 3. Laptop
`PCGuard.exe` chalayein, server URL + PIN dein, **Room ID** copy karein. Program **unlocked** shuru hota hai aur background mein chalta hai.

## 4. Phone
APK install karein (ya Chrome mein link kholein), server URL, Room ID aur PIN dalein.

- **Quick actions:** Unlock / Lock / Shut down.
- **Remote screen:** Low / Medium / High quality chun kar "Start remote screen".
  - tap = click, lamba dabana = right click, ungli ghumana = mouse move
  - do ungliyan upar-neeche = scroll
  - "Drag" on karein to ungli ghumane se select/drag hota hai
  - Keyboard / Keys buttons se likhna, Esc, Tab, Win, Alt+Tab, arrows, media keys
- **Volume:** slider, mute, aur Android app mein phone ke volume buttons.

## Security
- PIN network par nahi jata. Har lock/unlock/shutdown/session ek one-time challenge sign karke hota hai.
- Session ke dauran har mouse/keyboard/volume message HMAC + counter se sign hota hai (replay aur forgery ignore).
- **Screen AES-256-GCM se end-to-end encrypt hoti hai**, beech ka server ya Tailscale use nahi dekh sakta. Galat PIN wala phone frames khol nahi sakta (test mein verify kiya).
- 5 galat attempts ke baad lockout. Room ID ko password ki tarah secret rakhein.
- **Session ke dauran laptop par laal banner hamesha dikhta hai** ("Remote session active"). Ye jaan boojh kar hai taake ye app kisi ko chupke se dekhne ke liye istemal na ho. Sirf apne laptop par use karein.
- Session 90 second bina activity ke khud band ho jata hai.
- Failsafe: lock ke dauran server 5 minute na mile to laptop khud unlock ho jata hai. Lock screen par PC keyboard se PIN likh kar bhi unlock ho sakta hai.

## Hadood (limits)
- Windows ki UAC (Yes/No) screen, login screen aur protected video (Netflix wagera) phone par kali dikhti hain.
- Administrator wali windows control karne ke liye PCGuard.exe ko "Run as administrator" chalana parega.
- Lock sirf Windows login ke baad ka overlay hai (Safe Mode se bypass ho sakta hai). BitLocker on rakhein.
- Power-off laptop ko software on nahi kar sakta.
- Screen sirf primary monitor ki dikhti hai.

## Tests (optional)
    cd server && npm install && cd ..
    PORT=18080 node server/server.js &
    python3 tests/fake_agent.py &
    node tests/phone_client_test.js
(`pip install cryptography websockets pillow` chahiye.)
