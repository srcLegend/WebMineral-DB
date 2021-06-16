import csv
import operator
import re
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import count
from threading import Thread, Lock
from time import time

import requests
from bs4 import BeautifulSoup
# Requires Selenium package version >= 4.0.0.B4
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

@dataclass
class Mineral:
	name: str = None
	density: float = None
	hardness: float = None
	elements: dict = field(default_factory = dict)

	# Functions to check for duplicates (based on names)
	def __eq__(self, other):
		return (self.name == other.name)
	def __hash__(self):
		return hash(('name', self.name))

def generateHeaders(headers, periodicTable):
	""" Appends and returns a given headers list with all elements from the periodic table\n
		Requires a periodic table in CSV format, where headers are in row 1\r
		Columns must be: Atomic Number, Name, Symbol, Mass, etc..."""
	with open(periodicTable, 'r') as file:
		rows = csv.reader(file)
		whitespace = re.compile(r'\s*')
		for row in rows:
			if (rows.line_num == 1): continue
			headers.append(re.sub(whitespace, '', row[2]))

def generateMineral(links, baselinks, patterns, titles, settings, xpath):
	"""Generates mineral objects. Seems to be thread-safe so far\n
	   Needs list/dictionaries of links, dictionaries of search patterns, titles, WebDriver settings,\\
		along with values of CSS Selector of mineral list page, XPath of mineral data page,\\
		first mineral XPath ID and, optionally, a last mineral XPath ID"""
	if (settings['browser'] == "chrome"):
		options = webdriver.ChromeOptions()
		options.add_experimental_option('excludeSwitches', ['enable-logging'])
		options.headless = settings['headless']
		options.page_load_strategy = 'eager'
		services = webdriver.chrome.service.Service(executable_path = settings['chrome'])
	elif (settings['browser'] == "edge"):
		options = webdriver.EdgeOptions()
		options.use_chromium = True
		options.add_argument("log-level=3")
		options.headless = settings['headless']
		options.page_load_strategy = 'eager'
		services = webdriver.edge.service.Service(executable_path = settings['edge'])
	elif (settings['browser'] == "firefox"):
		options = webdriver.FirefoxOptions()
		options.add_argument("log-level=3")
		options.headless = settings['headless']
		options.page_load_strategy = 'eager'
		services = webdriver.firefox.service.Service(executable_path = settings['firefox'])

	global locks
	with (webdriver.Chrome(options = options, service = services) if (settings['browser'] == "chrome") else
		  webdriver.Edge(options = options, service = services) if (settings['browser'] == "edge") else
		  webdriver.Firefox(options = options, service = services) if (settings['browser'] == "firefox") else None) as driver:

		tempMinerals, tempSkipped = [], []
		for link in links:
			startTime = time()
			driver.get(link)
			wait = WebDriverWait(driver, settings['timeout'])
			try:
				wait.until(EC.presence_of_element_located((By.XPATH, xpath(1))))
			except TimeoutException:
				tempSkipped.append(link)
				continue

			found = {'elements': False}
			done = {'elements': False,
					'density':  False,
					'hardness': False}

			# Find and try to extract mineral name, skip link on AttributeError
			temp = driver.find_element(By.XPATH, xpath(1)).text
			m = re.search(patterns['name'], temp)
			try:
				# Check that the name doesn't contain unwanted characters
				temp = m.group(2).replace('(', '').replace(')', '')
				# Check that the name isn't excluded, skip link if it is
				if re.search(patterns['exclude'], temp):
					tempSkipped.append(link)
					continue
				mineral = Mineral(name = temp)
			except AttributeError:
				tempSkipped.append(link)
				continue

			# Start looking for and extract mineral data
			for i in count(2):
				try:
					temp = driver.find_element(By.XPATH, xpath(i)).text
					# Check for elements, and keep looking until we hit a separator
					if not done['elements']:
						if ((not found['elements']) and (titles['elements'] in temp.lower())):
							try:
								temphref = driver.find_element(By.XPATH, f"{xpath(i)}/td[1]/a")
								if (temphref.get_attribute('href') == (baselinks['elements'])):
									found['elements'] = True
									continue
							except NoSuchElementException:
								continue
						if found['elements']:
							m = re.search(patterns['element'], temp)
							try:
								#	   [element,	percentage]
								temp = [m.group(2), m.group(1)]
								if (not temp[0] in mineral.elements):
									mineral.elements[temp[0]] = float(temp[1])
								else:
									mineral.elements[temp[0]] += float(temp[1])
							except AttributeError:
								if (patterns['elementsSeparator'] in temp):
									done['elements'] = True
							finally:
								continue

					# Check for density
					if ((not done['density']) and (titles['density'] in temp.lower())):
						try:
							temphref = driver.find_element(By.XPATH, f"{xpath(i)}/td[1]/a")
							if (temphref.get_attribute('href') == (baselinks['density'])):
								m = re.search(patterns['density'], temp)
								mineral.density = float(m.group(1))
								done['density'] = True
						except NoSuchElementException:
							pass
						finally:
							continue

					# Check for hardness
					if ((not done['hardness']) and (titles['hardness'] in temp.lower())):
						try:
							temphref = driver.find_element(By.XPATH, f"{xpath(i)}/td[1]/a")
							if (temphref.get_attribute('href') == (baselinks['hardness'])):
								m = re.search(patterns['hardness'], temp)
								try:
									temp = m.group(1)
									mineral.hardness = float(temp)
								except ValueError:
									temp = list(map(float, temp.split(patterns['hardnessSeparator'])))
									mineral.hardness = sum(temp)/len(temp)
								finally:
									done['hardness'] = True
						except NoSuchElementException:
							pass
						finally:
							continue
				except NoSuchElementException:
					break

				if all(v == True for v in done.values()): break

			tempMinerals.append(mineral)
			# Lock printing for proper console output
			with locks['print']:
				print(f"Done downloading {mineral.name} in {time() - startTime:.2f} seconds")

	# Lock variables to avoid race conditions, then append them
	with locks['append']:
		global minerals
		global skipped
		minerals = [*minerals, *tempMinerals]
		skipped = [*skipped, *tempSkipped]

def generateMineralTest(links, baselinks, patterns, titles, settings, xpath):
	"""Generates mineral objects. Seems to be thread-safe so far\n
	   Needs list/dictionaries of links, dictionaries of search patterns, titles, WebDriver settings,\\
		along with values of CSS Selector of mineral list page, XPath of mineral data page,\\
		first mineral XPath ID and, optionally, a last mineral XPath ID"""
	if (settings['browser'] == "chrome"):
		options = webdriver.ChromeOptions()
		options.add_experimental_option('excludeSwitches', ['enable-logging'])
		options.headless = settings['headless']
		options.page_load_strategy = 'eager'
		services = webdriver.chrome.service.Service(executable_path = settings['chrome'])
	elif (settings['browser'] == "edge"):
		options = webdriver.EdgeOptions()
		options.use_chromium = True
		options.add_argument("log-level=3")
		options.headless = settings['headless']
		options.page_load_strategy = 'eager'
		services = webdriver.edge.service.Service(executable_path = settings['edge'])
	elif (settings['browser'] == "firefox"):
		options = webdriver.FirefoxOptions()
		options.add_argument("log-level=3")
		options.headless = settings['headless']
		options.page_load_strategy = 'eager'
		services = webdriver.firefox.service.Service(executable_path = settings['firefox'])

	global locks
	temp_minerals, temp_skipped = [], []
	for link in links:
		start_time = time()

		r = requests.get(link)
		s = BeautifulSoup(r.content, 'html.parser')

		# Find and try to extract mineral name
		try:
			temp = s.select("h3 > b")[0].contents[0]

			# Check that the name doesn't contain unwanted characters
			# Skip if it fits an exclude pattern
			m = re.search(patterns['name'], temp)
			temp = m.group(1).replace('(', '').replace(')', '').strip()
			if re.search(patterns['exclude'], temp):
				temp_skipped.append(link)
			else:
				mineral = Mineral(name = temp)
		except IndexError:
			if ("redirect" in s.contents[0].text.lower()):
				temp = s.contents[0].contents[1].contents[3].attrs['content']
				m = re.search("(http.*)", temp)
				links.append(m.group(1))
			else:
				temp_skipped.append(link)
			continue
		except AttributeError:
			temp_skipped.append(link)
			continue

		# Check for density
		if (temp := s.select("a[href*=\"../help/Density.shtml\"]")):
			temp = list(temp[0].parents)
			temp = list(temp[0].parent)
			temp = temp[3].contents[0]
			try:
				mineral.density = float(temp)
			except ValueError:
				m = re.search(patterns['density'], temp)
				mineral.density = float(m.group(1))

		temp_minerals.append(mineral)
		if (link == links[-1]):
			break
		continue

		# Start looking for and extract mineral data
		for i in count(2):
			try:
				temp = driver.find_element(By.XPATH, xpath(i)).text
				# Check for elements, and keep looking until we hit a separator
				if not done['elements']:
					if ((not found['elements']) and (titles['elements'] in temp.lower())):
						try:
							temphref = driver.find_element(By.XPATH, f"{xpath(i)}/td[1]/a")
							if (temphref.get_attribute('href') == (baselinks['elements'])):
								found['elements'] = True
								continue
						except NoSuchElementException:
							continue
					if found['elements']:
						m = re.search(patterns['element'], temp)
						try:
							#	   [element,	percentage]
							temp = [m.group(2), m.group(1)]
							if (not temp[0] in mineral.elements):
								mineral.elements[temp[0]] = float(temp[1])
							else:
								mineral.elements[temp[0]] += float(temp[1])
						except AttributeError:
							if (patterns['elementsSeparator'] in temp):
								done['elements'] = True
						finally:
							continue

				# Check for density
				if ((not done['density']) and (titles['density'] in temp.lower())):
					try:
						temphref = driver.find_element(By.XPATH, f"{xpath(i)}/td[1]/a")
						if (temphref.get_attribute('href') == (baselinks['density'])):
							m = re.search(patterns['density'], temp)
							mineral.density = float(m.group(1))
							done['density'] = True
					except NoSuchElementException:
						pass
					finally:
						continue

				# Check for hardness
				if ((not done['hardness']) and (titles['hardness'] in temp.lower())):
					try:
						temphref = driver.find_element(By.XPATH, f"{xpath(i)}/td[1]/a")
						if (temphref.get_attribute('href') == (baselinks['hardness'])):
							m = re.search(patterns['hardness'], temp)
							try:
								temp = m.group(1)
								mineral.hardness = float(temp)
							except ValueError:
								temp = list(map(float, temp.split(patterns['hardnessSeparator'])))
								mineral.hardness = sum(temp)/len(temp)
							finally:
								done['hardness'] = True
					except NoSuchElementException:
						pass
					finally:
						continue
			except NoSuchElementException:
				break

			if all(v == True for v in done.values()): break

		tempMinerals.append(mineral)
		# Lock printing for proper console output
		with locks['print']:
			print(f"Done downloading {mineral.name} in {time() - startTime:.2f} seconds")

	# Lock variables to avoid race conditions, then append them
	with locks['append']:
		global minerals
		global skipped
		minerals = [*minerals, *temp_minerals]
		skipped = [*skipped, *temp_skipped]

def generateMinerals(baselinks, patterns, titles, settings, xpath, cssSelector, firstMineral, lastMineral = None):
	"""Gathers links of all available minerals, then splits them into batches for threading\n
	   Needs dictionaries of links, search patterns, titles, WebDriver settings,\\
		along with values of CSS Selector of mineral list page, XPath of mineral data page,\\
		first mineral XPath ID and, optionally, a last mineral XPath ID"""
	if (settings['browser'] == "chrome"):
		options = webdriver.ChromeOptions()
		options.add_experimental_option('excludeSwitches', ['enable-logging'])
		options.headless = settings['headless']
		options.page_load_strategy = 'none'
		services = webdriver.chrome.service.Service(executable_path = settings['chrome'])
	elif (settings['browser'] == "edge"):
		options = webdriver.EdgeOptions()
		options.use_chromium = True
		options.add_argument("log-level=3")
		options.headless = settings['headless']
		options.page_load_strategy = 'none'
		services = webdriver.edge.service.Service(executable_path = settings['edge'])
	elif (settings['browser'] == "firefox"):
		options = webdriver.FirefoxOptions()
		options.add_argument("log-level=3")
		options.headless = settings['headless']
		options.page_load_strategy = 'none'
		services = webdriver.firefox.service.Service(executable_path = settings['firefox'])

	try:
		with (webdriver.Chrome(options = options, service = services) if (settings['browser'] == "chrome") else
			  webdriver.Edge(options = options, service = services) if (settings['browser'] == "edge") else
			  webdriver.Firefox(options = options, service = services) if (settings['browser'] == "firefox") else None) as driver:

			driver.get(baselinks['data'])
			wait = WebDriverWait(driver, settings['timeout'])
			wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, cssSelector(firstMineral))))

			links, tempSkipped = [], []
			for i in count(firstMineral):
				if ((lastMineral != None) and (i > lastMineral)): break
				# Try to get mineral link, skip otherwise
				try:
					temp = driver.find_element(By.CSS_SELECTOR, cssSelector(i)).get_attribute('href')
					if (".shtml" in temp):
						links.append(temp)
					else:
						tempSkipped.append(temp)
				except NoSuchElementException:
					break
				print(f"Acquiring links. Currently at link #{i - firstMineral}")

	except AttributeError:
		print(f"Chosen browser ({settings['browser']}) is not supported")

	# Append skipped links
	global skipped
	skipped = [*skipped, *tempSkipped]

	# Separate links into batches for threading, then start threads
	maxLinks = len(links)//settings['threads']
	remainingLinks = len(links)%settings['threads']
	slicers, threads = [0, 0], []
	for t in range(0, settings['threads']):
		slicers = [slicers[1], (t + 1)*maxLinks]
		if (remainingLinks > 0):
			remainingLinks -= 1
			slicers[1] += 1
		if ((t + 1) < settings['threads']):
			threads.append(Thread(target = generateMineralTest,
								  args = (links[slicers[0]:slicers[1]], baselinks, patterns, titles, settings, xpath)))
		else:
			threads.append(Thread(target = generateMineralTest,
								  args = (links[slicers[0]:], baselinks, patterns, titles, settings, xpath)))
		threads[-1].start()

	# Wait for thread completion
	for thread in threads:
		thread.join()

if (__name__ == "__main__"):
	# Whether to regenerate minerals database or not
	generate = True
	# Whether to overwrite certain minerals with custom values or not
	custom = False

	# Data files
	currentMinerals = "data/CurrentMinerals.csv"
	customMinerals = "data/CustomMinerals.csv"
	periodicTable = "data/PeriodicTable.csv"
	mineralsDatabase = "data/testing.csv" # "data/MineralsDatabase.csv"

	# CSV initial headers
	headers = ["Mineral", "Density", "Hardness"]
	generateHeaders(headers, periodicTable)

	if generate:
		# WebDriver settings
		settings = {'headless': True,
					'browser' : "edge", # Set to "edge", "chrome" or "firefox"
					'chrome'  : "bin/chromedriver.exe", # Get from "https://chromedriver.chromium.org/"
					'edge'	  : "bin/msedgedriver.exe", # Get from "https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/"
					'firefox' : "bin/geckodriver.exe",	# Get from "https://github.com/mozilla/geckodriver/releases"
					'timeout' : 30,
					'threads' : 1}

		baselink = "http://webmineral.com"
		baselinks = {'base'	   : baselink,
					 'data'	   : baselink + "/data/index.html",
					 'elements': baselink + "/help/Composition.shtml",
					 'density' : baselink + "/help/Density.shtml",
					 'hardness': baselink + "/help/Hardness.shtml"}

		titles = {'elements': "composition",
				  'density' : "density",
				  'hardness': "hardness"}

		# Mineral data page xpath
		xpath = lambda i: f"//*[@id=\"header\"]/tbody/tr/td/center/table[3]/tbody/tr[{i}]"
		if (settings['browser'] == "firefox"):
			xpath = lambda i: f"/html/body/table/tbody/tr/td/center/table[3]/tbody/tr[{i}]"

		# Minerals list page CSS Selector
		cssSelector = lambda i: f"body > table > tbody > tr:nth-child({i}) > td:nth-child(2) > a"
		if (settings['browser'] == "firefox"):
			cssSelector = lambda i: f"body > table:nth-child(2) > tbody:nth-child(1) > tr:nth-child({i}) > td:nth-child(2) > a:nth-child(1)]"

		# RegEx patterns. Check with "https://regexr.com/"
		patterns = {'name'			   : "General(.*)Information",			# Match group 1
					'exclude'		   : "(IMA\S*)",						# Match group 1
					'element'		   : "(\d+\.?\d*)\s*%\s*(\w+).*",		# Match group 1 for percentage, group 2 for element
					'density'		   : "(\d+\.?\d*)\s*$",				# Match group 1
					'hardness'		   : "(\d+\.?\d*-\d+\.?\d*|\d+\.?\d*)", # Match group 1
					'hardnessSeparator': "-",	   # In case of a hardness range value, takes the average as the hardness
					'elementsSeparator': "______"} # Signals end of element values

		# Lock object for threading
		locks = {'append': Lock(),
				 'print' : Lock()}
		minerals, skipped = [], []
		generateMinerals(baselinks, patterns, titles, settings, xpath, cssSelector, firstMineral = 4, lastMineral = 14)
		exit()
		# Keep track of duplicates
		duplicates = defaultdict(list)
		for m in minerals:
			duplicates[m.name].append(m)
		duplicates = {k: v for k, v in dict(sorted(duplicates.items())).items() if len(v) > 1}

		# Removes duplicates and returns a new sorted list
		minerals = list(set(minerals))
		minerals.sort(key = operator.attrgetter('name'))

		# Writes everything to a CSV file
		# Additionally keep track of minerals containing rare earth elements
		mineralsREE = []
		with open(mineralsDatabase, 'w', newline = '') as file:
			rows = csv.DictWriter(file, fieldnames = headers)
			rows.writeheader()
			for mineral in minerals:
				tempdict = {headers[0]:	mineral.name,
							headers[1]:	mineral.density,
							headers[2]: mineral.hardness}
				tempdict.update(mineral.elements)
				try:
					del tempdict['RE']
					mineralsREE.append(mineral)
				except KeyError:
					pass
				rows.writerow(tempdict)

		# Print duplicate minerals
		if duplicates:
			for duplicate in duplicates:
				print(f"Found duplicates of \"{duplicate}\", with these properties :")
				for d in duplicates[duplicate]:
					print(f"\tDensity {d.density}, Hardness {d.hardness}, Elements {d.elements}")

		# Print minerals containing rare earth elements
		if mineralsREE:
			if (len(mineralsREE) == 1):
				print(f"\"{mineralsREE[0].name}\" contains rare earth elements")
			else:
				print("These minerals contain rare earth elements :")
				for m in mineralsREE:
					print(f"\t {m.name}")

		# Print skipped links
		if skipped:
			if (len(skipped) == 1):
				print(f"\"{skipped[0]}\" was skipped")
			else:
				print("These were skipped :")
				for link in skipped:
					print(f"\t {link}")


	if custom:
		# Read minerals off of database if a new one isn't generated
		if not generate:
			minerals = []
			with open(mineralsDatabase, 'r') as file:
				rows = csv.DictReader(file, fieldnames = headers)
				for row in rows:
					if (rows.line_num == 1): continue

					minerals.append(Mineral(name = row[headers[0]]))
					if (row[headers[1]] != ''):
						minerals[-1].density = float(row[headers[1]])
					if (row[headers[2]] != ''):
						minerals[-1].hardness = float(row[headers[2]])

					for header in headers[3:]:
						if (row[header] != ''):
							minerals[-1].elements[header] = float(row[header])

		# Read custom mineral data, then separate them depending on whether they are new or modified
		custom, modified = [], []
		with open(customMinerals, 'r') as file:
			rows = csv.DictReader(file, fieldnames = headers)
			for row in rows:
				if (rows.line_num == 1): continue

				# Check if a custom mineral is already listed in the database
				# Append relevant list accordingly, then delete it
				mIndex = next((mIndex for (mIndex, mineral) in enumerate(minerals) if (mineral.name == row[headers[0]])), None)
				if mIndex:
					modified.append(minerals[mIndex])
					del minerals[mIndex]
				else:
					custom.append(Mineral(name = row[headers[0]]))
					if (row[headers[1]] != ''):
						custom[-1].density = float(row[headers[1]])
					if (row[headers[2]] != ''):
						custom[-1].hardness = float(row[headers[2]])

					for header in headers[3:]:
						if (row[header] != ''):
							custom[-1].elements[header] = float(row[header])

				minerals.append(Mineral(name = row[headers[0]]))
				if (row[headers[1]] != ''):
					minerals[-1].density = float(row[headers[1]])
				if (row[headers[2]] != ''):
					minerals[-1].hardness = float(row[headers[2]])

				for header in headers[3:]:
					if (row[header] != ''):
						minerals[-1].elements[header] = float(row[header])

		# Sort minerals by name
		minerals.sort(key = operator.attrgetter('name'))
		modified.sort(key = operator.attrgetter('name'))
		custom.sort(key = operator.attrgetter('name'))

		# Writes everything to a new CSV file
		with open(currentMinerals, 'w', newline = '') as file:
			rows = csv.DictWriter(file, fieldnames = headers)
			rows.writeheader()
			for mineral in minerals:
				tempdict = {headers[0]:	mineral.name,
							headers[1]:	mineral.density,
							headers[2]: mineral.hardness}
				tempdict.update(mineral.elements)
				try: del tempdict['RE']
				except KeyError: pass
				rows.writerow(tempdict)

		# Print modified minerals
		if modified:
			if (len(modified) == 1):
				print(f"{modified[0]} has been modified")
			else:
				print("These minerals have been modified :")
				for m in modified:
					print(f"\t{m.name}")

		# Print custom minerals
		if custom:
			if (len(custom) == 1):
				print(f"{custom[0]} has been added")
			else:
				print("These minerals have been added :")
				for c in custom:
					print(f"\t{c.name}")
