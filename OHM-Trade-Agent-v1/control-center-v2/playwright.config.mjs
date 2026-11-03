import {defineConfig} from '@playwright/test';
export default defineConfig({
 testDir:'./e2e',timeout:30000,retries:0,workers:1,
 use:{baseURL:'http://127.0.0.1:5173',browserName:'chromium',headless:true},
 webServer:{command:'npm run dev',url:'http://127.0.0.1:5173/dashboard/v2/',reuseExistingServer:false,timeout:30000},
 reporter:[['list']],
});
