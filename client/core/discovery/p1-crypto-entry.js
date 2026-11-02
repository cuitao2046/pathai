const canonicalize = require('canonicalize');
const { hmac } = require('@noble/hashes/hmac');
const { sha256 } = require('@noble/hashes/sha2');
const { utf8ToBytes } = require('@noble/hashes/utils');

module.exports = { canonicalize, hmac, sha256, utf8ToBytes };
