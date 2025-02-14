import discord
from discord import app_commands
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import logging
from typing import Literal
import os
from dotenv import load_dotenv
load_dotenv()
# Setup logging for debugging and error reporting
logging.basicConfig(level=logging.INFO)

# ----------------------
# Google Sheets Setup
# ----------------------
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = "durable-firefly-450917-c1-53efcba90315.json"  # Update if needed
SHEET_NAME = "TimeSheet"  # Must have headers: Officer Name, Discord ID, Clock-In Time, Clock-Out Time, Total Shift, Subdivision
#hehe

try:
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPE)
    gc = gspread.authorize(creds)
    sheet = gc.open(SHEET_NAME).sheet1  # Using the first worksheet
    logging.info("Successfully connected to Google Sheets.")
except Exception as e:
    logging.error("Error connecting to Google Sheets: %s", e)
    raise e  # Stop the bot if we can't connect

# ----------------------
# Discord Bot Setup
# ----------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory store for clocked-in users {user_id: clock_in_timestamp}
clocked_in_users = {}


# Helper function: update a single cell using update_cell (row, col, value)
def update_sheet_cell(row: int, col: int, value: str):
    try:
        sheet.update_cell(row, col, value)
    except Exception as e:
        logging.error("Error updating cell at row %s, col %s: %s", row, col, e)
        raise e


@bot.event
async def on_ready():
    # Sync slash commands with Discord
    await bot.tree.sync()
    logging.info(f"Logged in as {bot.user}")
    print(f"Logged in as {bot.user}")


# ----------------------
# Slash Command: Clock-In
# ----------------------
@bot.tree.command(name="clockin", description="Clock in with your details")
@app_commands.describe(
    callsign="Your callsign (e.g., 3210)",
    subdivision="Your subdivision (e.g., TEU)"
)
@app_commands.choices(subdivision=[
    app_commands.Choice(name="TEU", value="TEU"),
    app_commands.Choice(name="Investigations", value="Investigations"),
    app_commands.Choice(name="AMSU", value="AMSU"),
    app_commands.Choice(name="DUI", value="DUI"),
    app_commands.Choice(name="SRT", value="SRT"),
    app_commands.Choice(name="Offroad", value="Offroad"),
    app_commands.Choice(name="Parking", value="Parking"),
    app_commands.Choice(name="FrontDesk", value="FrontDesk"),
    app_commands.Choice(name="CO", value="CO")
])
async def clockin(interaction: discord.Interaction, callsign: str, subdivision: Literal[
    "TEU", "Investigations", "AMSU", "DUI", "SRT", "Offroad", "Parking", "FrontDesk", "CO"]):
    user_id = str(interaction.user.id)
    # Officer name is now just the callsign
    officer_name = callsign
    now = datetime.now()
    # Use format without seconds
    timestamp = now.strftime("%Y-%m-%d %H:%M")

    if user_id in clocked_in_users:
        await interaction.response.send_message(f"{interaction.user.mention}, you're already clocked in!",
                                                ephemeral=True)
        return

    try:
        clocked_in_users[user_id] = timestamp
        # Append a new row with six columns:
        # Officer Name, Discord ID, Clock-In Time, Clock-Out Time, Total Shift, Subdivision
        sheet.append_row([officer_name, f"'{user_id}", timestamp, "", "", subdivision])
        await interaction.response.send_message(
            f"{interaction.user.mention}, clocked in at {timestamp}. Subdivision: {subdivision}",
            ephemeral=False
        )
        logging.info(f"{officer_name} clocked in at {timestamp}.")
    except Exception as e:
        logging.error("Clock-in error: %s", e)
        await interaction.response.send_message("Error clocking in. Please try again.", ephemeral=True)


# ----------------------
# Slash Command: Clock-Out
# ----------------------
@bot.tree.command(name="clockout", description="Clock out, optionally specifying a time")
@app_commands.describe(
    time_str="Optional time in format YYYY-MM-DD HH:MM (default is current time)"
)
async def clockout(interaction: discord.Interaction, time_str: str = None):
    user_id = str(interaction.user.id)
    username = interaction.user.name

    # Determine clock-out time
    if time_str is None:
        now = datetime.now()
    else:
        try:
            now = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except Exception as e:
            await interaction.response.send_message("Invalid time format. Please use YYYY-MM-DD HH:MM.", ephemeral=True)
            return

    timestamp = now.strftime("%Y-%m-%d %H:%M")

    if user_id not in clocked_in_users:
        await interaction.response.send_message(f"{interaction.user.mention}, you are not clocked in!", ephemeral=True)
        return

    clockin_time_str = clocked_in_users.pop(user_id)

    try:
        clockin_time = datetime.strptime(clockin_time_str, "%Y-%m-%d %H:%M")
        shift_duration = now - clockin_time

        # Format shift_duration without decimals (HH:MM:SS)
        total_seconds = int(shift_duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        shift_duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"

        # Retrieve all records from the sheet
        records = sheet.get_all_records()
        row_to_update = None
        for idx, record in enumerate(records, start=2):
            rec_discord = str(record.get("Discord ID")).strip().lstrip("'")
            rec_clockin = record.get("Clock-In Time", "").strip()
            rec_clockout = record.get("Clock-Out Time", "").strip()
            if rec_discord == user_id and rec_clockin == clockin_time_str and rec_clockout == "":
                row_to_update = idx
                break

        if row_to_update is None:
            await interaction.response.send_message(
                f"{interaction.user.mention}, your clock-in record was not found in the sheet.", ephemeral=True
            )
            return

        logging.info("Updating row %s: setting Clock-Out Time to %s and Total Shift to %s",
                     row_to_update, timestamp, shift_duration_str)
        update_sheet_cell(row_to_update, 4, timestamp)
        update_sheet_cell(row_to_update, 5, shift_duration_str)

        await interaction.response.send_message(
            f"{interaction.user.mention}, you clocked out at {timestamp}. Shift duration: {shift_duration_str}.",
            ephemeral=False
        )
        logging.info(f"{username} clocked out at {timestamp} with a duration of {shift_duration_str}.")
    except Exception as e:
        logging.error("Error during clock-out for %s: %s", username, e)
        await interaction.response.send_message("An error occurred during clock-out. Please try again later.",
                                                ephemeral=True)


# ----------------------
# Run the Bot
#s

bot.run(os.getenv("DISCORD_BOT_TOKEN"))