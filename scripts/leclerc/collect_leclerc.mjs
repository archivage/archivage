#!/usr/bin/env node
// Shared by the Codex browser runtime and the standalone Playwright/CDP CLI.
import {createHash} from 'node:crypto';
import {mkdir, writeFile} from 'node:fs/promises';
import {homedir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

export const TICKETS_URL = 'https://www.e.leclerc/espace-client/carte-fidelite/tickets-de-caisse';

export async function collectReceipts(tab, output = join(homedir(), 'Archive/leclerc')) {
	const source_url = await tab.url();
	if (source_url?.split('?')[0] !== TICKETS_URL)
		throw new Error('Open the E.Leclerc receipt list in the authenticated browser first.');
	const close = tab.playwright.getByRole('img', {name: 'close modal', exact: true});
	if (await close.isVisible()) await close.click();
	const buttons = tab.playwright.getByRole('button', {name: 'Consulter', exact: true});
	await buttons.first().waitFor({state: 'visible', timeoutMs: 15000});
	const count = await buttons.count();
	// The website returns at most ten results. Never report a saturated view as complete.
	if (count >= 10)
		throw new Error('The view has 10 tickets and may be truncated. Narrow the date filters and rerun.');
	const filters = await tab.playwright.evaluate(() => ({
		min_date: document.getElementById('min-date')?.value,
		max_date: document.getElementById('max-date')?.value,
	}));
	const sources = join(output, 'sources');
	await mkdir(sources, {recursive: true, mode: 0o700});
	const result = [];
	for (let i = 0; i < count; i++) {
		await buttons.nth(i).click();
		try {
			const table = tab.playwright.getByRole('table');
			await table.waitFor({state: 'visible', timeoutMs: 15000});
			const content = await table.evaluate(el => ({raw_text: el.innerText, html: el.outerHTML}));
			const match = content.raw_text.match(/^\s*(99[0-9A-Z]{18,30})\s*$/m);
			if (!match || !/Total\s+\d+\s+articles?/.test(content.raw_text.replaceAll('\u00a0', ' ')))
				throw new Error('Ticket content is incomplete or its printed ID is missing.');
			const ticket_id = match[1];
			const sha256 = createHash('sha256')
				.update(JSON.stringify([content.raw_text, content.html])).digest('hex');
			const file = join(sources, `${ticket_id}-${sha256.slice(0, 16)}.json`);
			const source = {
				schema_version: 1, ticket_id, source_url, captured_at: new Date().toISOString(),
				filters, sha256, ...content,
			};
			try {
				await writeFile(file, JSON.stringify(source, null, 2) + '\n', {flag: 'wx', mode: 0o600});
			} catch (error) {
				if (error.code !== 'EEXIST') throw error;
			}
			result.push({ticket_id, file});
		} finally {
			await close.click();
			await tab.playwright.getByRole('table').waitFor({state: 'hidden', timeoutMs: 15000});
		}
	}
	const batch = {captured_at: new Date().toISOString(), source_url, filters, visible_count: count, receipts: result};
	await mkdir(join(output, 'batches'), {recursive: true, mode: 0o700});
	await writeFile(join(output, 'batches', `${batch.captured_at.replaceAll(':', '-')}.json`),
		JSON.stringify(batch, null, 2) + '\n', {flag: 'wx', mode: 0o600});
	return batch;
}

async function main() {
	const args = process.argv.slice(2);
	if (args.includes('--help')) {
		console.log('collect_leclerc.mjs [--archive-dir PATH] [--cdp-url http://127.0.0.1:9222]\n' +
			'Requires Playwright and an authenticated E.Leclerc receipt tab. Collects the current filtered view.');
		return;
	}
	let output = join(homedir(), 'Archive/leclerc');
	let endpoint = 'http://127.0.0.1:9222';
	for (let i = 0; i < args.length; i += 2) {
		if (!args[i + 1]) throw new Error(`Missing value for ${args[i]}`);
		if (args[i] === '--archive-dir') output = resolve(args[i + 1]);
		else if (args[i] === '--cdp-url') endpoint = args[i + 1];
		else throw new Error(`Unknown option: ${args[i]}`);
	}
	const address = new URL(endpoint);
	if (!['127.0.0.1', 'localhost', '[::1]'].includes(address.hostname))
		throw new Error('Use a loopback CDP endpoint for the personal browser.');
	const {chromium} = await import('playwright-core');
	const browser = await chromium.connectOverCDP(endpoint);
	const pages = browser.contexts().flatMap(context => context.pages())
		.filter(page => page.url().split('?')[0] === TICKETS_URL);
	if (pages.length !== 1)
		throw new Error(`Found ${pages.length} E.Leclerc receipt tabs; keep exactly one open.`);
	const page = pages[0];
	// Codex's browser facade calls timeout "timeoutMs"; translate it for Playwright.
	const adapt = locator => new Proxy(locator, {
		get(target, key) {
			if (key === 'waitFor') return ({timeoutMs, ...options}) => target.waitFor({...options, timeout: timeoutMs});
			if (['nth', 'first', 'getByRole'].includes(key))
				return (...params) => adapt(target[key](...params));
			return typeof target[key] === 'function' ? target[key].bind(target) : target[key];
		},
	});
	const tab = {url: () => page.url(), playwright: {
		getByRole: (...params) => adapt(page.getByRole(...params)),
		evaluate: fn => page.evaluate(fn),
	}};
	console.log(JSON.stringify(await collectReceipts(tab, output), null, 2));
	// Disconnect the client by exiting; never close the user's Chrome or its tabs.
}

if (typeof process !== 'undefined' && process.argv[1] &&
	import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
	main().then(() => process.exit(0)).catch(error => {
		console.error(error.message);
		process.exit(1);
	});
}
