# -*- coding: utf-8 -*-

import re
import emoji
from twisted.internet import reactor
from buffer import Buffer
import util

try:
    from discord_webhook import DiscordEmbed
except Exception as e:
    pass

class Player(object):
    def __init__(self, client, name, team, match, skin, gm, isDev):
        self.client = client
        self.server = client.server
        self.match = match
        self.skin = skin
        self.gameMode = gm
        self.isDev = isDev
        
        self.name = ' '.join(emoji.emojize(re.sub(r"[^\x00-\x7F]+", "", emoji.demojize(name)).strip())[:20].split()).upper()
        self.team = team
        if len(self.team) > 0 and not isDev and util.checkCurse(self.name):
            self.name = str()
        if len(self.name) == 0:
            self.name = self.server.defaultName if self.client.username != "" else ("【G】"+self.server.defaultName)
        elif len(self.client.username) == 0:
            self.name = "【G】" + self.name
        if isDev:
            self.name = "【𝐃𝐄𝐕】" + self.name
        elif self.skin in [52]:
            self.skin = 0
        self.pendingWorld = None
        self.level = int()
        self.zone = int()
        self.posX = int()
        self.posY = int()
        self.dead = True
        self.win = bool()
        self.voted = bool()
        self.loaded = bool()
        self.lobbier = bool()
        self.lastUpdatePkt = None
        self.wins = 0
        self.deaths = 0
        self.kills = 0
        self.coins = 0

        self.trustCount = int()
        self.lastX = int()
        self.lastXOk = True
        
        self.id = match.addPlayer(self)

    def sendJSON(self, j):
        self.client.sendJSON(j)

    def sendText(self, t):
        self.client.sendText(t)

    def sendBin(self, code, b):
        self.client.sendBin(code, b)

    def getSimpleData(self):
        return {"id": self.id, "name": self.name, "team": self.team}

    def serializePlayerObject(self):
        return Buffer().writeInt16(self.id).writeInt8(self.level).writeInt8(self.zone).writeShor2(self.posX, self.posY).writeInt16(self.skin).writeInt8(self.isDev).toBytes()

    def loadWorld(self, worldName, loadMsg):
        self.dead = True
        self.loaded = False
        self.pendingWorld = worldName
        self.sendText(loadMsg)
        self.client.startDCTimer(15)

    def setStartTimer(self, time):
        self.sendJSON({"packets": [
            {"time": time, "type": "g13"}
        ], "type": "s01"})

    def onEnterIngame(self):
        if not self.dead:
            return
        
        self.lobbier = self.match.isLobby

        self.match.onPlayerEnter(self)
        self.loadWorld(self.match.world, self.match.getLoadMsg())
        if (self.server.enableLevelSelectInMultiPrivate or self.team == "") and self.match.private:
            self.sendLevelSelect()

    def sendLevelSelect(self):
        levelList = self.server.getLevelList("game", self.match.levelMode)
        levelDicts = [{"shortId":self.server.levels[x]["shortname"], "longId":x} for x in levelList]
        levelDicts.sort(key=lambda x: x["shortId"])
        self.sendJSON({"type": "gll", "levels": levelDicts})

    def onLoadComplete(self):
        if self.loaded or self.pendingWorld is None:
            return

        self.client.stopDCTimer()
        
        self.lobbier = self.match.isLobby
        self.level = 0
        self.zone = 0
        self.posX = 35
        self.posY = 3
        self.win = False
        self.dead = False
        self.loaded = True
        self.pendingWorld = None
        self.lastXOk = True
        
        self.sendBin(0x02, Buffer().writeInt16(self.id).writeInt16(self.skin).writeInt8(self.isDev)) # ASSIGN_PID

        self.match.onPlayerReady(self)

    def handlePkt(self, code, b, pktData):
        if code == 0x10: # CREATE_PLAYER_OBJECT
            level, zone, pos = b.readInt8(), b.readInt8(), b.readShor2()
            self.level = level
            self.zone = zone
            self.posX = pos[0]
            self.posY = pos[1]

            self.dead = False
            self.client.stopDCTimer()
            self.match.broadBin(0x10, self.serializePlayerObject())

        elif code == 0x11: # KILL_PLAYER_OBJECT
            if self.dead or self.win:
                return

            self.dead = True
            self.client.startDCTimer(60)

            self.addDeath()
            self.match.broadBin(0x11, Buffer().writeInt16(self.id))
            
        elif code == 0x12: # UPDATE_PLAYER_OBJECT
            if self.dead or self.lastUpdatePkt == pktData:
                return

            level, zone, pos, sprite, reverse = b.readInt8(), b.readInt8(), b.readVec2(), b.readInt8(), b.readBool()

            if self.level != level or self.zone != zone:
                self.match.onPlayerWarp(self, level, zone)
                
            self.level = level
            self.zone = zone
            self.posX = pos[0]
            self.posY = pos[1]
            self.lastUpdatePkt = pktData

            if sprite > 5 and self.match.world == "lobby" and zone == 0:
                self.client.block(0x1)
                return
            
            self.match.broadPlayerUpdate(self, pktData)
            
        elif code == 0x13: # PLAYER_OBJECT_EVENT
            if self.dead or self.win:
                return

            type = b.readInt8()

            if self.match.world == "lobby":
                self.client.block(0x2)
                return
            
            self.match.broadBin(0x13, Buffer().writeInt16(self.id).write(pktData))

        elif code == 0x17:
            killer = b.readInt16()
            if self.id == killer:
                return
            
            killer = self.match.getPlayer(killer)
            if killer is None:
                return

            killer.addKill()
            killer.sendBin(0x17, Buffer().writeInt16(self.id).write(pktData))

        elif code == 0x18: # PLAYER_RESULT_REQUEST
            if self.dead or self.win:
                return

            self.win = True
            self.client.startDCTimer(120)

            pos = self.match.getWinners()
            if pos == 1:
                self.addWin()
            try:
                # Maybe this should be assynchronous?
                if self.server.discordWebhook is not None and pos == 1 and not self.match.private:
                    name = self.name
                    # We already filter players that have a squad so...
                    if len(self.team) == 0 and not isDev and util.checkCurse(self.name):
                        name = "[ censored ]"
                    embed = DiscordEmbed(description='**%s** has achieved **#1** victory royale!%s' % (name, " (PVP Mode)" if self.gameMode == 1 else " (Hell mode)" if self.gameMode == 2 else ""), color=0xffff00)
                    self.server.discordWebhook.add_embed(embed)
                    self.server.discordWebhook.execute()
                    self.server.discordWebhook.remove_embed(0)
            except:
                pass

            # Make sure that everyone knows that the player is at the axe
            self.match.broadPlayerUpdate(self, self.lastUpdatePkt)
            
            self.match.broadBin(0x18, Buffer().writeInt16(self.id).writeInt8(pos).writeInt8(0))
            
        elif code == 0x19:
            self.trustCount += 1
            if self.trustCount > 8:
                self.client.block(0x3)

        elif code == 0x20: # OBJECT_EVENT_TRIGGER
            if self.dead:
                return

            self.match.objectEventTrigger(self, b, pktData)
            
        elif code == 0x30: # TILE_EVENT_TRIGGER
            if self.dead:
                return

            self.match.tileEventTrigger(self, b, pktData)

    def addCoin(self):
        if not self.lobbier:
            self.coins += 1
        self.sendBin(0x21, Buffer().writeInt8(0))

    def addWin(self):
        if not self.lobbier:
            self.wins += 1
			self.coins += 200

    def addDeath(self):
        if not self.lobbier:
            self.deaths += 1
			self.coins -= 10

    def addKill(self):
        if not self.lobbier:
            self.kills += 1
			self.coins += 10

