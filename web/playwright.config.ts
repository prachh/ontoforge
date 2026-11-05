import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',timeout:60000,workers:1,use:{baseURL:process.env.PLAYWRIGHT_BASE_URL||'http://127.0.0.1:8017',headless:true,viewport:{width:1440,height:1080},launchOptions:{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}},reporter:[['list'],['json',{outputFile:'../artifacts/browser-results.json'}]]});
