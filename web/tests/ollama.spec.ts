import {test,expect} from '@playwright/test';
test.skip(!process.env.ONTO_TEST_OLLAMA,'Opt in with ONTO_TEST_OLLAMA=1 against a configured local Ollama server.');
test('Ollama settings report actual local model connectivity',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Model provider',exact:true})).toBeVisible();
 await expect(page.getByText('Ollama',{exact:true})).toBeVisible();
 await expect(page.getByText('Local Ollama needs no API key.',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'Check Ollama connection',exact:true}).click();
 await expect(page.getByText('Ollama is reachable and the selected model is installed.',{exact:true})).toBeVisible();
 await page.screenshot({path:'artifacts/10-ollama-settings.png',fullPage:true});
});
