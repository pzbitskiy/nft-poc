import base64
import json
import pathlib
import time

from algosdk import encoding
from algosdk import kmd
from algosdk.future import transaction
from algosdk.v2client import algod

from sqlitedict import SqliteDict

DICT_FILE = str(pathlib.Path(__file__).parent.absolute() / 'db.sqlite')
TEAL_PATH = pathlib.Path(__file__).parent.absolute() / 'teal'

def to_bytes(num):
    if num == 0:
        return b'\x00'

    length = 0
    val = num
    while val != 0:
        val = val >> 8
        length += 1
    return num.to_bytes(length, 'big')

class NftApp:
    TOKENS_PER_SHARD = 60

    def __init__(self, params):
        self._password = ''
        self._kcl = kmd.KMDClient(params['kmd_token'], params['kmd_address'])
        res = self._kcl.list_wallets()
        self._wh = self._kcl.init_wallet_handle(res[0]['id'], self._password)
        self._manager = self._kcl.list_keys(self._wh)[0]

        self._algod = algod.AlgodClient(params['algod_token'], params['algod_address'])

        # main app storage
        # TOKENS_PER_SHARD int entries for sharding db
        # 1 bytes entry for app creator
        # 1 int entry for app idx
        # 1 int entry for allocated shards
        # 1 int entry for allocated tokens
        num_uints = self.TOKENS_PER_SHARD + 3
        self._global_schema = transaction.StateSchema(num_uints=num_uints, num_byte_slices=1)
        # user storage
        # nft name -> count
        self._local_schema = transaction.StateSchema(num_uints=1)

        # shard app storage
        # 60 bytes entries for txnid + owner
        # 1 int entry for main app idx
        # 1 int entry for this shard index
        # 1 bytes entry for app creator
        num_byte_slices = self.TOKENS_PER_SHARD + 1
        self._shard_global_schema = transaction.StateSchema(num_uints=2, num_byte_slices=num_byte_slices)
        self._shard_local_schema = transaction.StateSchema()

        self._app_id = None

    def create(self):
        with open(str(TEAL_PATH / 'approval.teal'), 'r') as fin:
            src = fin.read()
        approval = base64.b64decode(self._algod.compile(src)['result'])

        with open(str(TEAL_PATH / 'clearstate.teal'), 'r') as fin:
            src = fin.read()
        clearstate = base64.b64decode(self._algod.compile(src)['result'])

        sp = self._algod.suggested_params()
        txn = transaction.ApplicationCreateTxn(
            self._manager, sp, on_complete=0,
            approval_program=approval, clear_program=clearstate,
            global_schema=self._global_schema,
            local_schema=self._local_schema,
            app_args=[b'create']
        )
        stxn = self._kcl.sign_transaction(self._wh, self._password, txn)
        txn_id = self._algod.send_transaction(stxn)
        while True:
            res = self._algod.pending_transaction_info(txn_id)
            if 'application-index' in res:
                self._app_id = int(res['application-index'])
                break
            time.sleep(5)

        with SqliteDict(DICT_FILE) as db:
            db['main_app_id'] = self._app_id
            db['shards'] = 0
            db['tokens'] = 0
            db.commit()

    def add_shard(self):
        with open(str(TEAL_PATH / 'shard.teal'), 'r') as fin:
            src = fin.read()
        shard_app = base64.b64decode(self._algod.compile(src)['result'])

        with open(str(TEAL_PATH / 'clearstate.teal'), 'r') as fin:
            src = fin.read()
        clearstate = base64.b64decode(self._algod.compile(src)['result'])

        with SqliteDict(DICT_FILE) as db:
            main_app_id = db['main_app_id']
            shard = db['shards']

        sp = self._algod.suggested_params()
        txn = transaction.ApplicationCreateTxn(
            self._manager, sp, on_complete=0,
            approval_program=shard_app, clear_program=clearstate,
            global_schema=self._shard_global_schema,
            local_schema=self._shard_local_schema,
            app_args=[b'shard_create', to_bytes(main_app_id), to_bytes(shard)]
        )
        stxn = self._kcl.sign_transaction(self._wh, self._password, txn)
        txn_id = self._algod.send_transaction(stxn)

        while True:
            res = self._algod.pending_transaction_info(txn_id)
            if 'application-index' in res:
                shard_app_id = int(res['application-index'])
                break
            time.sleep(5)

        txn = transaction.ApplicationNoOpTxn(
            self._manager, sp, main_app_id,
            app_args=[b'shard_create', to_bytes(shard_app_id), to_bytes(shard)]
        )
        stxn = self._kcl.sign_transaction(self._wh, self._password, txn)
        txn_id = self._algod.send_transaction(stxn)

        with SqliteDict(DICT_FILE) as db:
            db[f's{shard}'] = shard_app_id
            shard += 1
            db['shards'] = shard
            db.commit()

    # token operations
    def mint(self, meta):
        with SqliteDict(DICT_FILE) as db:
            main_app_id = db['main_app_id']
            next_token = db['tokens']

        note = json.dumps(meta).encode('utf8')
        sp = self._algod.suggested_params()
        txn = transaction.PaymentTxn(self._manager, sp, self._manager, 0, note=note)
        stxn = self._kcl.sign_transaction(self._wh, self._password, txn)
        txn_id = self._algod.send_transaction(stxn)

        shard = self._get_shard_from_token(next_token)

        with SqliteDict(DICT_FILE) as db:
            shard_app_id = db[f's{shard}']

        # main app verifies pre-conditions
        main_txn = transaction.ApplicationNoOpTxn(
            self._manager, sp, main_app_id,
            app_args=[b'mint', to_bytes(next_token), to_bytes(shard)],
            foreign_apps=[shard_app_id]
        )
        # shard app writes the token
        shard_txn = transaction.ApplicationNoOpTxn(
            self._manager, sp, shard_app_id,
            app_args=[b'mint', to_bytes(next_token), txn_id.encode('utf8')],
            foreign_apps=[main_app_id]
        )

        self._sign_send_manager(main_txn, shard_txn)

        # gid = transaction.calculate_group_id([main_txn, shard_txn])
        # main_txn.group = gid
        # shard_txn.group = gid

        # main_stxn = self._kcl.sign_transaction(self._wh, self._password, main_txn)
        # shard_stxn = self._kcl.sign_transaction(self._wh, self._password, shard_txn)
        # self._algod.send_transactions([main_stxn, shard_stxn])

        with SqliteDict(DICT_FILE) as db:
            db[next_token] = dict(meta=note)
            next_token += 1
            db['tokens'] = next_token
            db.commit()

    def _get_shard_from_token(self, token):
        return token // self.TOKENS_PER_SHARD

    def _sign_send_manager(self, *txns):
        gid = transaction.calculate_group_id(txns)
        signed = []
        for txn in txns:
            txn.group = gid
            stxn = self._kcl.sign_transaction(self._wh, self._password, txn)
            signed.append(stxn)
        self._algod.send_transactions(signed)

    def _sign_send_sk(self, sk, *txns):
        gid = transaction.calculate_group_id(txns)
        signed = []
        for txn in txns:
            txn.group = gid
            stxn = txn.sign(sk)
            signed.append(stxn)
        self._algod.send_transactions(signed)

    def safe_transfer_from(self, to, frm, tok, frm_sk=None):
        # TODO: lookup blockchain for tokens
        with SqliteDict(DICT_FILE) as db:
            main_app_id = db['main_app_id']
            tokens = db['tokens']
            if tok > tokens:
                raise LookupError(f'token {tok} does not exist')
            token_info = db[tok]

        owner = token_info.get('owner', None)
        shard = self._get_shard_from_token(tok)
        with SqliteDict(DICT_FILE) as db:
            shard_app_id = db[f's{shard}']
        sp = self._algod.suggested_params()

        # main app verifies pre-conditions
        main_txn = transaction.ApplicationNoOpTxn(
            frm, sp, main_app_id,
            app_args=['transfer', to_bytes(tok), to_bytes(shard), encoding.decode_address(to)],
            foreign_apps=[shard_app_id]
        )
        # shard app updates ownership of the token
        shard_txn = transaction.ApplicationNoOpTxn(
            frm, sp, shard_app_id,
            app_args=['transfer', to_bytes(tok), encoding.decode_address(to)],
            foreign_apps=[main_app_id]
        )
        # update user local store?
        # main_txn_upd = transaction.ApplicationNoOpTxn(
        #     frm, sp, main_app_id,
        #     app_args=['transfer_upd', to_bytes(tok), to_bytes(shard), encoding.decode_address(to)],
        #     foreign_apps=[shard_app_id]
        # )

        if not owner:
            self._sign_send_manager(main_txn, shard_txn)
        else:
            if owner != frm:
                raise RuntimeError('current owner does not match sender')
            self._sign_send_sk(frm_sk, main_txn, shard_txn)

        token_info['owner'] = to
        with SqliteDict(DICT_FILE) as db:
            db[tok] = token_info
            db.commit()

    def balance_of(self, owner) -> int:
        pass

    def owner_of(self, tok) -> str:
        pass

    def name(self) -> str:
        return 'Algo NFT PoC'

    def symbol(self) -> str:
        return 'ANFTP'

    def token_meta(self, tok) -> str:
        # TODO: lookup blockchain with indexer
        # lookup for tok, then txn id from meta
        # then return txn.note as json

        with SqliteDict(DICT_FILE) as db:
            return db[tok]['meta']


def main():
    a = NftApp(dict(
        kmd_token='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        kmd_address='http://127.0.0.1:60001',
        algod_token='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        algod_address='http://127.0.0.1:60000',
    ))
    a.create()
    print('created')

    a.add_shard()
    print('addded shard')

    a.mint({'name': 'token1'})
    print('minted')

if __name__ == '__main__':
    main()