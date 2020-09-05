import stdlib.const
import common

const thisShardIdKey = "s"

function approval() {
    if txn.ApplicationID == 0 {
        // this must be a standalone transaction
        if global.GroupSize != 1 || txn.NumAppArgs != 3 || txn.ApplicationArgs[0] != opShardCreate {
            return 0
        }
        let appId = btoi(txn.ApplicationArgs[1])
        let shardId = btoi(txn.ApplicationArgs[2])
        apps[0].put(appIdKey, appId)
        apps[0].put(thisShardIdKey, shardId)
        apps[0].put(creatorKey, txn.Sender)
        return 1
    }

    if txn.OnCompletion != AcNoOp {
        return 0
    }

    if txn.ApplicationArgs[0] == opMint {
        // must be second in a group
        if global.GroupSize != 2 || txn.GroupIndex != 1 {
            return 0
        }
        if txn.NumAppArgs != 3 {
            return 0
        }
        if txn.ApplicationArgs[0] != gtxn[0].ApplicationArgs[0] || txn.ApplicationArgs[1] != gtxn[0].ApplicationArgs[1] {
            return 0
        }
        if apps[0].get(creatorKey) != txn.Sender {
            return 0
        }

        // ensure shard is a right one
        let tokenIn = txn.ApplicationArgs[1]
        let token = btoi(tokenIn)
        let thisShardId = apps[0].get(thisShardIdKey)
        if token / tokensPerShard != thisShardId {
            return 0
        }

        // create token
        apps[0].put(tokenIn, txn.ApplicationArgs[2])

        return 1
    }

    if txn.ApplicationArgs[0] == opTransfer {
        // must be second in a group
        if global.GroupSize != 2 || txn.GroupIndex != 1 {
            return 0
        }
        if txn.NumAppArgs != 3 {
            return 0
        }
        if txn.ApplicationArgs[0] != gtxn[0].ApplicationArgs[0] || txn.ApplicationArgs[1] != gtxn[0].ApplicationArgs[1] {
            return 0
        }

        let tokenIn = txn.ApplicationArgs[1]
        let token = btoi(tokenIn)
        let thisShardId = apps[0].get(thisShardIdKey)
        if token / tokensPerShard != thisShardId {
            return 0
        }
        let value, exist = apps[0].getEx(tokenIn)
        if !exist {
            return 0
        }
        let to = txn.ApplicationArgs[2]
        if len(value) == 32 {
            // non-owned token
            if txn.Sender != apps[0].get(creatorKey) {
                return 0
            }
            let newValue = concat(value, to)
            apps[0].put(tokenIn, newValue)
        } else {
            // pre-owned token
            let meta = substring(value, 0, 32)
            let owner = substring(value, 32, 64)
            if txn.Sender != owner {
                return 0
            }
            let newValue = concat(meta, to)
            apps[0].put(tokenIn, newValue)
        }
        return 1
    }
    return 0
}