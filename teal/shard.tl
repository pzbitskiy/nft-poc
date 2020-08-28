import common

const thisShardIdKey = "s"

function approval() {
    if txn.ApplicationID == 0 {
        // this must be a standalone transaction
        if global.GroupSize != 1 || txn.NumAppArgs != 3 || txn.ApplicationArgs[0] != "shard_create" {
            return 0
        }
        let appId = btoi(txn.ApplicationArgs[1])
        let shardId = btoi(txn.ApplicationArgs[2])
        app_global_put(appIdKey, appId)
        app_global_put(thisShardIdKey, shardId)
        app_global_put(creatorKey, txn.Sender)
        return 1
    }

    if txn.OnCompletion != 0 {
        return 0
    }

    if txn.ApplicationArgs[0] == "mint" {
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
        if app_global_get(creatorKey) != txn.Sender {
            return 0
        }

        // ensure shard is a right one
        let tokenIn = txn.ApplicationArgs[1]
        let token = btoi(tokenIn)
        let thisShardId = app_global_get(thisShardIdKey)
        if token / tokensPerShard != thisShardId {
            return 0
        }

        // create token
        app_global_put(tokenIn, txn.ApplicationArgs[2])

        return 1
    }

    if txn.ApplicationArgs[0] == "transfer" {
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
        let thisShardId = app_global_get(thisShardIdKey)
        if token / tokensPerShard != thisShardId {
            return 0
        }
        let value, exist = app_global_get_ex(0, tokenIn)
        if !exist {
            return 0
        }
        let to = txn.ApplicationArgs[2]
        if len(value) == 32 {
            // non-owned token
            if txn.Sender != app_global_get(creatorKey) {
                return 0
            }
            let newValue = concat(value, to)
            app_global_put(tokenIn, newValue)
        } else {
            // pre-owned token
            let meta = substring3(value, 0, 32)
            let owner = substring3(value, 32, 64)
            if txn.Sender != owner {
                return 0
            }
            let newValue = concat(meta, to)
            app_global_put(tokenIn, newValue)
        }
        return 1
    }
    return 0
}