const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const m2pPublicFilePem = "D:\\Apaar\\M2P_keys\\node\\m2ppub.pem";
const busPrivateFile = "D:\\Apaar\\M2P_keys\\node\\prepaid.transcorpint.com.pem";

const plainText = process.argv[2] || '{"test":"data"}';
const key = process.argv[3] || '1234123412341278';
const iv = process.argv[4] || '1234123412341279';

// 1. AES encrypt
function encrypt(text, key, iv) {
    let cipher = crypto.createCipheriv('aes-128-cbc', Buffer.from(key), iv);
    let encrypted = cipher.update(text);
    encrypted = Buffer.concat([encrypted, cipher.final()]);
    return encrypted.toString('base64');
}

// 2. RSA encrypt key
function encryptKey(sessionKey) {
    var pubkey = crypto.createPublicKey(Buffer.from(fs.readFileSync(m2pPublicFilePem)));
    var encryptedKey = crypto.publicEncrypt({key:pubkey, padding: crypto.constants.RSA_PKCS1_PADDING}, Buffer.from(sessionKey)).toString('base64');
    return encryptedKey;
}

// 3. RSA sign
function signJson(requestData) {
    var privateKeyfs = fs.readFileSync(busPrivateFile, "utf8");
    var privateKey = crypto.createPrivateKey({
        'key': privateKeyfs,
        'format': 'pem',
        'passphrase': '12345'
    });
    var signToken = crypto.createSign('sha1WithRSAEncryption');
    signToken.write(requestData);
    signToken.end();
    var token = signToken.sign({ 'key': privateKey, 'passphrase': '1234' }, 'base64');
    return token;
}

const body = encrypt(plainText, key, iv);
const encKey = encryptKey(key);
const token = signJson(plainText);
const entity = encryptKey("TRANSCORP");

console.log(JSON.stringify({
    body: body,
    key: encKey,
    token: token,
    entity: entity,
    refNo: iv
}));
