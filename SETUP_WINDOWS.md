# Setup guide for Windows (no coding needed)

About 15 minutes. You do steps 1–3 once to get it running on your PC, step 4 to see it on your
phone, step 5 only if you want Telegram alerts.

## 1. Install three free programs

| Program | Where | Note |
|---|---|---|
| **Python** | https://www.python.org/downloads/ | On the first screen tick **"Add python.exe to PATH"**, then "Install Now" |
| **Git for Windows** | https://git-scm.com/download/win | Click "Next" on every screen |
| **Google Chrome** | you probably have it | Microsoft Edge also works |

## 2. Install Gold Mine (one command)

Open the `gold-mine` folder and **double-click `setup.bat`**.

It creates its own Python environment, installs the components, creates the database and
schedules the two daily scans (07:00 and 14:00, Tunis time). When you see `Terminé`, press a key.

> If Windows shows a blue "Windows protected your PC" box, click **More info → Run anyway**.

## 3. First scan

1. **Double-click `panel.bat`**. Your browser opens the control panel at `http://localhost:5000`.
   Keep the black window open while you use the panel.
2. Click **▶ Lancer un scan**. The log at the bottom moves by itself. A scan takes 10–30 minutes
   and you can keep using the PC.
3. When it says `terminé`, click **Tableau de bord ↗** at the top to see the result on your PC.

From now on the scans run by themselves twice a day, as long as the PC is on and you are logged
in. A scan missed because the PC was off starts when you turn it back on.

## 4. Put the dashboard on your phone (GitHub Pages)

1. Create a free account on https://github.com.
2. Click **+ → New repository**. Name: `gold-mine`. Choose **Public**. Do not tick anything else.
   Click **Create repository**.
3. Copy the address shown, it looks like `https://github.com/your-name/gold-mine.git`.
4. In the `gold-mine` folder **double-click `github.bat`**, paste the address, press Enter.
   A GitHub window opens the first time: sign in. Wait for `C'est fait`.
5. On GitHub, open your repository → **Settings → Pages**. Under "Branch" choose **main** and the
   folder **/docs**, click **Save**.
6. After a minute or two your dashboard is at `https://your-name.github.io/gold-mine/`.
   Open it on your phone and add it to the home screen.

After every scan the new data is sent there automatically.

> The free site is public: anyone with the link can see it. Your settings file (and the Telegram
> token) stays on your PC and is never uploaded.

## 5. Telegram alerts (optional)

1. In Telegram, talk to **@BotFather**, send `/newbot`, follow the two questions. Copy the
   **token** it gives you (a long line with a `:` in it).
2. Open your new bot and send it any message (so it is allowed to write to you).
3. Talk to **@userinfobot**: it replies with your **Id** (a number).
4. In the control panel → **Réglages → Alertes Telegram**: tick the box, paste the token and the
   Id, choose the score (75 is a good start), click **Enregistrer**, then
   **Envoyer un message Telegram de test**.

## Everyday use

- **Phone**: open the dashboard, pick the country at the top, play with **Réglages**.
- **PC**: `panel.bat` when you want to add keywords, a country, a competitor page, or your
  supplier prices.

## If something goes wrong

| What you see | What to do |
|---|---|
| `Python n'est pas installe` | Reinstall Python and tick "Add python.exe to PATH" |
| Scan ends with `partiel (Facebook a bloqué)` | Normal now and then. It kept what it read. If it happens every time, lower "Pubs à lire par scan" in Réglages and wait a day |
| No scan at 07:00 | The PC must be on with your session open. Check Scans → "Planification". Saving Réglages re-creates the schedule |
| Dashboard on the phone is not updating | Open the panel → Scans and read the "Publication" column. If it says the connection failed, run `github.bat` again |
| A product mixes two different things | Panel → Produits → open it → tick the wrong ads → "Séparer" |
| The same product appears twice | Panel → Produits → tick both → "Fusionner" |
| Prices look wrong for a country | Panel → Pays → check "Façons d'écrire la monnaie" and the plausible min/max price |
| You moved the folder | Double-click `setup.bat` again |

## Uninstall

Open "Task Scheduler" in Windows, delete the task named **GoldMine**, then delete the folder.
