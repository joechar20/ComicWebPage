#python -m pip install urllib3
#python -m pip install requests
#python -m pip install bs4
import urllib3
import requests
import pathlib
import ssl
import datetime
import sys
ssl._create_default_https_context = ssl._create_unverified_context
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timedelta

# synology NAS (LAB_NAS) path
image_folder = '/volume1/Comics/'

# Check if there is a date argument if not collect current date -1 day
# Get arguments excluding the script name (sys.argv[0])
arguments = sys.argv[1:]

# Check if there are exactly 3 arguments year month day collect that date
if len(arguments) == 3:

	# set the date to match the arguments
	# convert Input parameters Year Month Day to INT
	year_param = int(sys.argv[1])
	month_param = int(sys.argv[2])
	day_param = int(sys.argv[3])

	#set the cur_date variable 
	cur_date = datetime(year_param, month_param, day_param)
	cur_date = cur_date.date()

# no date arguments passed collect current date -1 day
else:
	# set date to yesterday
	cur_date = datetime.now() - timedelta(days=1)
	cur_date = cur_date.date()

# build date formats for urls
cur_date.strftime('%B-%D-%Y')
mon = cur_date.strftime('%B')  # month name ie July, August
mm = cur_date.strftime('%m')  # month with zero pad
dayz = cur_date.strftime('%d').lstrip('0')  # day without zero pad
day = cur_date.strftime('%d')  # day with zero pad
year = cur_date.strftime('%Y')  # year with century

datestr01 = year + '-' + mm + '-' + day
datestr02 = year + '/' + mm + '/' + day
datestr03 = mon + '-' + dayz + '-' + year
print(cur_date)
print('datestr01:' + datestr01);
print('datestr02:' + datestr02);
print('datestr03:' + datestr03);

comics_list = [
	# Comic_Title, Comic URL(slash at end), Comic_Date_Format

	['1andertoons', 'https://www.gocomics.com/andertoons/', datestr02],  # pre-empt with 1 for portrit view
	['1carpediem', 'https://comicskingdom.com/carpe-diem/', datestr01],  # pre-empt with 1 for portrit view
	['1dennisthemenace', 'https://comicskingdom.com/dennis-the-menace/', datestr01],  # pre-empt with 1 for portrit view
	['1familycircus', 'https://comicskingdom.com/family-circus/', datestr01],  # pre-empt with 1 for portrit view
	['1looseparts', 'https://www.gocomics.com/looseparts/', datestr02],  # pre-empt with 1 for portrit view
	['1Marmaduke', 'https://www.gocomics.com/marmaduke/', datestr02],  # pre-empt with 1 for portrit view
	['babyblues', 'https://www.gocomics.com/babyblues/', datestr02],
	['bc', 'https://www.gocomics.com/bc/', datestr02],
	['beetlebailey', 'https://comicskingdom.com/beetle-bailey-1/', datestr01],
	['bleeker', 'https://www.gocomics.com/bleeker/', datestr02],
	['blondie', 'https://www.comicskingdom.com/blondie/', datestr01],
	['born-loser', 'https://www.gocomics.com/the-born-loser/', datestr02],
	['calvinandhobbes', 'https://www.gocomics.com/calvinandhobbes/', datestr02],
	['dogsofckennel', 'https://www.gocomics.com/dogsofckennel/', datestr02],
	['fminus', 'https://www.gocomics.com/fminus/', datestr02],
	['frazz', 'https://www.gocomics.com/frazz/', datestr02],
	['garfield', 'https://www.gocomics.com/garfield/', datestr02],
	['hagarthehorrible', 'https://comicskingdom.com/hagar-the-horrible/', datestr01],
	['overthehedge', 'https://www.gocomics.com/overthehedge/', datestr02],
	['peanuts', 'https://www.gocomics.com/peanuts/', datestr02],
	['pearlsbeforeswine', 'https://www.gocomics.com/pearlsbeforeswine/', datestr02],
	['pickles', 'https://www.gocomics.com/pickles/', datestr02],
	['wizardofid', 'https://www.gocomics.com/wizardofid/', datestr02],
	['zitscomics', 'https://comicskingdom.com/zits/', datestr01],

]

# User Agent Headers
user_headers = "Mozilla/5.0"

# fallback image URL
fallback_image_url = 'https://upload.wikimedia.org/wikipedia/commons/7/73/Grays_and_Torreys_Peaks_2006-08-06.jpg'

# get comic url
for comic in comics_list:
	print(comic[0])	
	pageurl = comic[1]+comic[2]+'/'

	# build filename and path
	filename = comic[0]+'_'+cur_date.strftime('%Y%m%d')+".jpeg"
	filenamepath = Path(image_folder, filename)

	#try loop parse URL, Get IMage, Save image - print all errors
	errors = []
	try:
		thepage = requests.get(pageurl, user_headers)
		soup = BeautifulSoup(thepage.text, "html.parser")
	except Exception as e:
		errors.append(e)
		print("* * * * * * * * Something went wrong with requesting the url * * * * * * * * ")
		print(pageurl)

	try: #get image url
		if "gocomics" in comic[1]:
			print("***gocomics url search***")
			meta_data = soup.find('link', attrs={'rel': 'preload', 'as': 'image'})
			imageurlA = meta_data.get('imagesrcset')
			imageurl = imageurlA.partition("?")[0]
		else:
			# get image url
			meta_data = (soup.find('meta', {"property": "og:image"}))
			imageurl = meta_data.get('content')
	except Exception as e:
		errors.append(e)
		print("* * * * * * * * Something went wrong with requesting the image url * * * * * * * * ")
		try:
			imageurl = fallback_image_url
		except:
			print("* * * * * * * * Something went wrong with requesting the image url * * * * * * * * ")

	try:
		# save image
		res = urllib3.request('GET',imageurl)
		imagefile = open(str(filenamepath), 'wb')
		imagefile.write(res.data)
		imagefile.close()
	except Exception as e:
		errors.append(e)
		print("* * * * * * * * Something went wrong with GETting and \ or saving the image  file * * * * * * * * ")
	
	# print all errormessages encountered for that comic
	if errors:
		print("Encountered the following errors:")
		for error in errors:
			print(f"- {error}")
	else:
		try:
			print(pageurl)
		except:
			print("* * * * * * * * Something went wrong with print pageurl * * * * * * * * ")

		try:
			print(imageurl)
		except:
			print("* * * * * * * * Something went wrong with print imageurl * * * * * * * * ")

		try:
			print(filenamepath)
		except:
			print("* * * * * * * * Something went wrong with print filenamepath * * * * * * * * ")
		
