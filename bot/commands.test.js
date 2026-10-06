import test, { mock } from 'node:test';
import assert from 'node:assert/strict';
import { REST } from 'discord.js';
import { recordCommand, registerCommands, validateRequest } from './commands.js';

test('slash command has start/end subcommands and is restricted by default', () => {
  const command = recordCommand();
  assert.equal(command.name, 'record');
  assert.deepEqual(command.options.map(o => o.name), ['start', 'end']);
  assert.equal(command.dm_permission, false);
  assert.equal(command.default_member_permissions, '32');
  assert.equal(recordCommand('role').default_member_permissions, null);
});
test('commands require configured guild, permission, and same VC side chat', () => {
  const valid = { guildId: 'g', expectedGuildId: 'g', channelType: 2, channelId: 'v', voiceChannelId: 'v', canManage: true };
  assert.equal(validateRequest(valid), null);
  assert.match(validateRequest({ ...valid, channelType: 0 }), /side chat/);
  assert.match(validateRequest({ ...valid, voiceChannelId: 'elsewhere' }), /side chat/);
  assert.match(validateRequest({ ...valid, canManage: false }), /Manage Server/);
  assert.match(validateRequest({ ...valid, guildId: 'other' }), /not configured/);
  assert.equal(validateRequest({ ...valid, canManage: false, hasRole: true }), null);
});

test('registration upserts only /record at the configured guild endpoint', async () => {
  const post = mock.method(REST.prototype, 'post', async () => ({}));
  try {
    await registerCommands({ DISCORD_TOKEN: 'test-bot-token', DISCORD_APPLICATION_ID: '123', DISCORD_GUILD_ID: '456' });
    assert.equal(post.mock.callCount(), 1);
    const [route, options] = post.mock.calls[0].arguments;
    assert.equal(route, '/applications/123/guilds/456/commands');
    assert.equal(options.body.name, 'record');
    assert.deepEqual(options.body.options.map(o => o.name), ['start', 'end']);
    await assert.rejects(registerCommands({}), /Missing DISCORD_TOKEN/);
  } finally { post.mock.restore(); }
});
