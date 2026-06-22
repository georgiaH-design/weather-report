
name: Daily Maritime Weather Report

on:
  schedule:
    # 07:00 UTC = 03:00 EDT (UTC-4) every morning
    - cron: '0 7 * * *'
  # Allow manual trigger from GitHub Actions tab
  workflow_dispatch:

jobs:
  generate-report:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Generate weather report HTML
        env:
          WINDY_API_KEY: ${{ secrets.WINDY_API_KEY }}
        run: python generate_weather.py

      - name: Install Playwright + Chromium
        run: |
          pip install playwright
          playwright install chromium --with-deps

      - name: Screenshot report as PNG
        run: python screenshot.py

      - name: Commit and push updated report
        run: |
          git config user.name "Captain Georgia Weather Bot"
          git config user.email "actions@github.com"
          git pull origin main
          git add index.html weather_report.png
          git diff --staged --quiet || git commit -m "Daily weather report — $(date -u '+%Y-%m-%d %H:%M UTC')"
          git push
