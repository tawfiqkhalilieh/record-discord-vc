import { PermissionFlagsBits, REST, Routes, SlashCommandBuilder } from 'discord.js';

export function recordCommand(roleId = '') {
  return new SlashCommandBuilder()
    .setName('record')
    .setDescription('Record the current voice channel (operator must prepare capture first)')
    .setDMPermission(false)
    .setDefaultMemberPermissions(roleId ? null : PermissionFlagsBits.ManageGuild)
    .addSubcommand(c => c.setName('start').setDescription('Start recording this voice call'))
    .addSubcommand(c => c.setName('end').setDescription('Stop, process, and save this recording'))
    .toJSON();
}

export async function registerCommands(env = process.env) {
  for (const key of ['DISCORD_TOKEN', 'DISCORD_APPLICATION_ID', 'DISCORD_GUILD_ID']) {
    if (!env[key]) throw new Error(`Missing ${key}`);
  }
  const rest = new REST({ version: '10' }).setToken(env.DISCORD_TOKEN);
  // POST upserts only our command and preserves commands belonging to other modules.
  await rest.post(Routes.applicationGuildCommands(env.DISCORD_APPLICATION_ID, env.DISCORD_GUILD_ID), {
    body: recordCommand(env.RECORD_ROLE_ID),
  });
  console.log('Registered /record start and /record end in configured guild.');
}

export function validateRequest({ guildId, expectedGuildId, channelType, channelId, voiceChannelId, canManage, hasRole }) {
  if (guildId !== expectedGuildId) return 'This server is not configured for recording.';
  if (!canManage && !hasRole) return 'You need Manage Server or the configured recording role.';
  if (channelType !== 2 || channelId !== voiceChannelId) {
    return 'Join a voice channel and run this command in that voice channel’s side chat.';
  }
  return null;
}
