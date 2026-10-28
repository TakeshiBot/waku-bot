import {configureWebhook,credentials,failed,readConfig,secrets} from './common.mjs';
try {await configureWebhook(await credentials(),await readConfig(),await secrets());}
catch(error) {failed(error);}
