IMAGE FOLDER

Use this folder for project images that you may want to change later.

Current files:
- zyra-mark.svg : brand mark used in login/header
- zyra-logo.svg : full brand logo
- zyra-logo.png : PNG version of the full logo

Current interview bot image:
- The avatar still uses the URL set in templates/main.html via AVATAR_IMAGE_URL

If you want a local bot image later:
1. Put the file here, for example: interview-bot.png
2. Update AVATAR_IMAGE_URL in templates/main.html to:
   /static/images/interview-bot.png
