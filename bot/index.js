import { Client, Events, GatewayIntentBits, MessageFlags, PermissionFlagsBits } from 'discord.js';
import { registerCommands, validateRequest } from './commands.js';

const env = process.env;
if (!env.RECORDER_API_KEY) throw new Error('Missing RECORDER_API_KEY');
const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates] });
const api = async (path, method = 'GET', body) => {
  const response = await fetch(`${env.RECORDER_URL || 'http://localhost:8000'}${path}`, {
    method, headers: { Authorization: `Bearer ${env.RECORDER_API_KEY}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(60000),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Recorder returned ${response.status}`);
  return data;
};
const roster = channel => [...channel.members.values()]
  .filter(member => !member.user.bot && member.id !== env.RECORDER_USER_ID)
  .map(member => ({ id: member.id, name: member.displayName }));

let syncing = false;
async function syncRoster() {
  if (syncing) return;
  syncing = true;
  try {
    const state = await api('/state');
    if (state.status !== 'recording') return;
    const guild = client.guilds.cache.get(state.guild_id);
    const channel = guild?.channels.cache.get(state.channel_id);
    if (channel) await api(`/sessions/${state.id}/participants`, 'PUT', { participants: roster(channel) });
  } catch (error) { console.error('Roster sync:', error.message); }
  finally { syncing = false; }
}

client.on(Events.VoiceStateUpdate, () => { void syncRoster(); });
client.once(Events.ClientReady, () => {
  console.log(`Bot ready as ${client.user.tag}`);
  setInterval(() => { void syncRoster(); }, 5000).unref();
});
client.on(Events.Error, error => console.error('Discord client error:', error.message));

client.on(Events.InteractionCreate, async interaction => {
  if (!interaction.isChatInputCommand() || interaction.commandName !== 'record') return;
  await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  try {
    const member = await interaction.guild?.members.fetch(interaction.user.id);
    const reason = validateRequest({
      guildId: interaction.guildId, expectedGuildId: env.DISCORD_GUILD_ID,
      channelType: interaction.channel?.type, channelId: interaction.channelId,
      voiceChannelId: member?.voice.channelId,
      canManage: member?.permissions.has(PermissionFlagsBits.ManageGuild),
      hasRole: Boolean(env.RECORD_ROLE_ID && member?.roles.cache.has(env.RECORD_ROLE_ID)),
    });
    if (reason) return await interaction.editReply(reason);
    const action = interaction.options.getSubcommand();
    if (action === 'start') {
      const session = await api('/sessions', 'POST', {
        guild_id: interaction.guildId, channel_id: interaction.channelId,
        channel_name: interaction.channel.name, requested_by: interaction.user.id,
        participants: roster(interaction.channel),
      });
      await interaction.editReply(`Recording started. ID: ${session.id}`);
      await interaction.followUp({ content: '🔴 Recording has started. Received webcams, watched screenshares, and call audio are being saved.', allowedMentions: { parse: [] } });
    } else {
      const session = await api('/end', 'POST', { guild_id: interaction.guildId, channel_id: interaction.channelId });
      const link = `${(env.PUBLIC_BASE_URL || 'http://localhost:3000').replace(/\/$/, '')}/recordings/${session.id}`;
      await interaction.editReply(`Recording stopped; processing and upload queued. Open the stash after processing: ${link}`);
      await interaction.followUp({ content: '⏹️ Recording has stopped. The video is processing for the admin stash.', allowedMentions: { parse: [] } });
    }
  } catch (error) {
    console.error('Recording command:', error.message);
    await interaction.editReply(`Unable to complete recording command: ${error.message}`);
  }
});

await registerCommands();
await client.login(env.DISCORD_TOKEN);
