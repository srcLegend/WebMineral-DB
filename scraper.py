import csv
import operator
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import count
from threading import Thread, Lock
from time import time

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
			threads.append(Thread(target = generateMineral,
								  args = (links[slicers[0]:slicers[1]], baselinks, patterns, titles, settings, xpath)))
		else:
			threads.append(Thread(target = generateMineral,
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
	periodicTable = "data/PeriodicTable.csv"
	currentMinerals = "data/CurrentMinerals.csv"
	mineralsDatabase = "data/MineralsDatabase.csv"
	customMinerals = "data/CustomMinerals.csv"

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
					'threads' : 8}

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
		patterns = {'name'			   : "(General )(.*)( Information)",	# Match group 2
					'exclude'		   : "(IMA\d+-?\d*)",					# Test group 1
					'element'		   : "(\d+\.?\d*)\s*%\s*(\w+).*",		# Match group 1 for percentage, group 2 for element
					'density'		   : "(\d+\.?\d*)$",					# Match group 1
					'hardness'		   : "(\d+\.?\d*-\d+\.?\d*|\d+\.?\d*)", # Match group 1
					'hardnessSeparator': "-",	   # In case of a hardness range value, takes the average as the hardness
					'elementsSeparator': "______"} # Signals end of element values

		# Lock object for threading
		locks = {'append': Lock(),
				 'print' : Lock()}
		minerals, skipped = [], []
		generateMinerals(baselinks, patterns, titles, settings, xpath, cssSelector, firstMineral = 4)

		# Keep dict of duplicates
		duplicates = defaultdict(list)
		for m in minerals:
			duplicates[m.name].append(m)
		duplicates = {k: v for k, v in duplicates.items() if len(v) > 1}

		# Removes duplicates and returns a new sorted list
		minerals = list(set(minerals))
		minerals.sort(key = operator.attrgetter('name'))

		# Writes everything to a CSV file
		with open(mineralsDatabase, 'w', newline = '') as file:
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

		# Print skipped links
		if skipped:
			if (len(skipped) == 1):
				print(f"\"{skipped[0]}\" was skipped")
			else:
				print("These were skipped :")
				for link in skipped:
					print(f"\t {link}")

		# Print duplicate minerals
		if duplicates:
			for duplicate in duplicates:
				print(f"Found duplicates of \"{duplicate}\", with these properties :")
				for d in duplicates[duplicate]:
					print(f"\tDensity {d.density}, Hardness {d.hardness}, Elements {d.elements}")

	if custom:
		if not generate:
			minerals = []
			with open(mineralsDatabase, 'r') as file:
				rows = csv.DictReader(file, fieldnames = headers)
				for row in rows:
					if (rows.line_num == 1): continue

					minerals.append(Mineral(name = row[headers[0]]))
					if (row[headers[1]] != ''):
						minerals[-1].density = row[headers[1]]
					if (row[headers[2]] != ''):
						minerals[-1].hardness = row[headers[2]]

					for header in headers[3:]:
						if (row[header] != ''):
							minerals[-1].elements[header] = row[header]

		with open(customMinerals, 'r') as file:
			rows = rows = csv.DictReader(file, fieldnames = headers)
			for row in rows:
				if (rows.line_num == 1): continue

				# Check if a custom mineral is already listed. Delete it if so
				mIndex = next((mIndex for (mIndex, mineral) in enumerate(minerals) if (mineral.name == row[headers[0]])), None)
				if mIndex: del minerals[mIndex]

				minerals.append(Mineral(name = row[headers[0]]))
				if (row[headers[1]] != ''):
					minerals[-1].density = row[headers[1]]
				if (row[headers[2]] != ''):
					minerals[-1].hardness = row[headers[2]]

				for header in headers[3:]:
					if (row[header] != ''):
						minerals[-1].elements[header] = row[header]

		minerals.sort(key = operator.attrgetter('name'))

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

"""
	These were skipped :
			http://webmineral.com/data/JCapplet.class
			http://webmineral.com/data/JCapplet.class.old1
			http://webmineral.com/data/IMA2007-036.shtml
			http://webmineral.com/data/IMA2007-041T.shtml
			http://webmineral.com/data/IMA2007-047.shtml
			http://webmineral.com/data/IMA2007-058.shtml
			http://webmineral.com/data/IMA2008-006.shtml
			http://webmineral.com/data/IMA2008-009.shtml
			http://webmineral.com/data/IMA2008-010.shtml
			http://webmineral.com/data/IMA2008-022.shtml
			http://webmineral.com/data/IMA2008-024.shtml
			http://webmineral.com/data/IMA2008-029.shtml
			http://webmineral.com/data/IMA2008-032.shtml
			http://webmineral.com/data/IMA2008-035.shtml
			http://webmineral.com/data/IMA2008-039.shtml
			http://webmineral.com/data/IMA2008-040.shtml
			http://webmineral.com/data/IMA2008-046.shtml
			http://webmineral.com/data/IMA2008-047.shtml
			http://webmineral.com/data/IMA2008-048.shtml
			http://webmineral.com/data/IMA2008-053.shtml
			http://webmineral.com/data/IMA2008-054.shtml
			http://webmineral.com/data/IMA2008-055.shtml
			http://webmineral.com/data/IMA2008-056.shtml
			http://webmineral.com/data/IMA2008-058.shtml
			http://webmineral.com/data/IMA2008-060.shtml
			http://webmineral.com/data/IMA2008-063.shtml
			http://webmineral.com/data/IMA2008-064.shtml
			http://webmineral.com/data/IMA2008-065.shtml
			http://webmineral.com/data/IMA2008-066.shtml
			http://webmineral.com/data/IMA2008-067.shtml
			http://webmineral.com/data/IMA2008-068.shtml
			http://webmineral.com/data/IMA2008-069.shtml
			http://webmineral.com/data/IMA2008-070.shtml
			http://webmineral.com/data/IMA2009-001.shtml
			http://webmineral.com/data/IMA2009-002.shtml
			http://webmineral.com/data/IMA2009-004.shtml
			http://webmineral.com/data/IMA2009-005.shtml
			http://webmineral.com/data/IMA2009-008.shtml
			http://webmineral.com/data/IMA2009-009.shtml
			http://webmineral.com/data/IMA2009-010.shtml
			http://webmineral.com/data/IMA2009-011.shtml
			http://webmineral.com/data/IMA2009-012.shtml
			http://webmineral.com/data/IMA2009-013.shtml
			http://webmineral.com/data/IMA2009-014.shtml
			http://webmineral.com/data/IMA2009-015.shtml
			http://webmineral.com/data/IMA2009-016.shtml
			http://webmineral.com/data/M%c3%a1traite.shtml
			http://webmineral.com/data/Wattevillite.shtml
			http://webmineral.com/data/Zincbl%c3%b6dite.shtml
			http://webmineral.com/data/Davidite.shtml
			http://webmineral.com/data/IMA2000-016.shtml
			http://webmineral.com/data/IMA2000-020.shtml
			http://webmineral.com/data/IMA2000-026.shtml
			http://webmineral.com/data/IMA2002-034.shtml
			http://webmineral.com/data/IMA2003-019.shtml
			http://webmineral.com/data/IMA2005-036.shtml
			http://webmineral.com/data/IMA2007-010.shtml
			http://webmineral.com/data/IMA2007-015.shtml
	Found duplicates of "Malanite", with these properties :
			Density 7.4, Hardness 5.0, Elements {'Cu': 10.95, 'Ir': 16.56, 'Pt': 50.4, 'S': 22.09}
			Density 7.4, Hardness 5.0, Elements {'Cu': 10.95, 'Ir': 16.56, 'Pt': 50.4, 'S': 22.09}
			Density 7.4, Hardness 5.0, Elements {'Cu': 10.95, 'Ir': 16.56, 'Pt': 50.4, 'S': 22.09}
	Found duplicates of "Maleevite", with these properties :
			Density 3.78, Hardness 7.0, Elements {'Ba': 39.76, 'Si': 16.51, 'B': 6.29, 'O': 37.44}
			Density 3.78, Hardness 7.0, Elements {'Ba': 39.76, 'Si': 16.51, 'B': 6.29, 'O': 37.44}
	Found duplicates of "Malhmoodite", with these properties :
			Density None, Hardness 3.0, Elements {'Zr': 22.3, 'Fe': 13.65, 'P': 15.14, 'H': 1.97, 'O': 46.93}
			Density None, Hardness 3.0, Elements {'Zr': 22.3, 'Fe': 13.65, 'P': 15.14, 'H': 1.97, 'O': 46.93}
			Density None, Hardness 3.0, Elements {'Zr': 22.3, 'Fe': 13.65, 'P': 15.14, 'H': 1.97, 'O': 46.93}
			Density None, Hardness 3.0, Elements {'Zr': 22.3, 'Fe': 13.65, 'P': 15.14, 'H': 1.97, 'O': 46.93}
	Found duplicates of "Malinkoite", with these properties :
			Density 2.9, Hardness 5.0, Elements {'Na': 18.26, 'Si': 22.31, 'B': 8.59, 'O': 50.84}
			Density 2.9, Hardness 5.0, Elements {'Na': 18.26, 'Si': 22.31, 'B': 8.59, 'O': 50.84}
	Found duplicates of "Mallardite", with these properties :
			Density 1.846, Hardness 2.0, Elements {'Mn': 19.83, 'H': 5.09, 'S': 11.57, 'O': 63.51}
			Density 1.846, Hardness 2.0, Elements {'Mn': 19.83, 'H': 5.09, 'S': 11.57, 'O': 63.51}
	Found duplicates of "Mallestigite", with these properties :
			Density None, Hardness 4.0, Elements {'Sb': 10.18, 'As': 5.81, 'H': 1.07, 'Pb': 55.83, 'S': 3.16, 'O': 23.95}
			Density None, Hardness 4.0, Elements {'Sb': 10.18, 'As': 5.81, 'H': 1.07, 'Pb': 55.83, 'S': 3.16, 'O': 23.95}
	Found duplicates of "Malyshevite", with these properties :
			Density None, Hardness None, Elements {'Cu': 13.37, 'Bi': 43.98, 'Pd': 22.4, 'S': 20.25}
			Density None, Hardness None, Elements {'Cu': 13.37, 'Bi': 43.98, 'Pd': 22.4, 'S': 20.25}
	Found duplicates of "Manaksite", with these properties :
			Density 2.73, Hardness 5.0, Elements {'K': 10.04, 'Na': 5.9, 'Mn': 14.11, 'Si': 28.85, 'O': 41.09}
			Density 2.73, Hardness 5.0, Elements {'K': 10.04, 'Na': 5.9, 'Mn': 14.11, 'Si': 28.85, 'O': 41.09}
	Found duplicates of "Mandarinoite", with these properties :
			Density 2.93, Hardness 2.5, Elements {'Fe': 18.6, 'H': 2.01, 'Se': 39.44, 'O': 39.95}
			Density 2.93, Hardness 2.5, Elements {'Fe': 18.6, 'H': 2.01, 'Se': 39.44, 'O': 39.95}
	Found duplicates of "Manganoneptunite", with these properties :
			Density 3.23, Hardness 5.5, Elements {'K': 4.31, 'Na': 5.07, 'Li': 0.77, 'Ti': 10.56, 'Mn': 9.09, 'Fe': 3.08, 'Si': 24.78, 'O': 42.35}
			Density 3.23, Hardness 5.5, Elements {'K': 4.31, 'Na': 5.07, 'Li': 0.77, 'Ti': 10.56, 'Mn': 9.09, 'Fe': 3.08, 'Si': 24.78, 'O': 42.35}
	Found duplicates of "Axinite-Mn", with these properties :
			Density 3.28, Hardness 6.75, Elements {'Ca': 14.08, 'Mn': 9.65, 'Al': 9.48, 'Si': 19.74, 'B': 1.9, 'H': 0.18, 'O': 44.97}
			Density 3.28, Hardness 6.75, Elements {'Ca': 14.08, 'Mn': 9.65, 'Al': 9.48, 'Si': 19.74, 'B': 1.9, 'H': 0.18, 'O': 44.97}
			Density 3.28, Hardness 6.75, Elements {'Ca': 14.08, 'Mn': 9.65, 'Al': 9.48, 'Si': 19.74, 'B': 1.9, 'H': 0.18, 'O': 44.97}
	Found duplicates of "Alabandite", with these properties :
			Density 3.99, Hardness 3.75, Elements {'Mn': 63.14, 'S': 36.86}
			Density 3.99, Hardness 3.75, Elements {'Mn': 63.14, 'S': 36.86}
	Found duplicates of "Manganohornesite", with these properties :
			Density 2.64, Hardness 1.0, Elements {'Mg': 3.23, 'Mn': 21.92, 'As': 26.58, 'H': 2.86, 'O': 45.4}
			Density 2.64, Hardness 1.0, Elements {'Mg': 3.23, 'Mn': 21.92, 'As': 26.58, 'H': 2.86, 'O': 45.4}
	Found duplicates of "Manganoshadlunite", with these properties :
			Density 4.44, Hardness 4.0, Elements {'Mn': 4.89, 'Fe': 13.26, 'Cu': 45.26, 'Pb': 6.15, 'S': 30.45}
			Density 4.44, Hardness 4.0, Elements {'Mn': 4.89, 'Fe': 13.26, 'Cu': 45.26, 'Pb': 6.15, 'S': 30.45}
	Found duplicates of "Mangangordonite", with these properties :
			Density 2.36, Hardness 3.0, Elements {'Mg': 0.2, 'Mn': 8.0, 'Al': 11.78, 'Fe': 3.48, 'P': 12.88, 'H': 3.77, 'O': 59.88}
			Density 2.36, Hardness 3.0, Elements {'Mg': 0.2, 'Mn': 8.0, 'Al': 11.78, 'Fe': 3.48, 'P': 12.88, 'H': 3.77, 'O': 59.88}
	Found duplicates of "Manganiandrosite-Ce", with these properties :
			Density None, Hardness None, Elements {'Sr': 1.02, 'Ca': 2.81, 'La': 5.33, 'Ce': 10.75, 'Sm': 0.25, 'Mg': 0.16, 'Ti': 0.8, 'Mn': 20.43, 'Al': 4.5, 'Fe': 2.14, 'Si': 14.05, 'H': 0.17, 'Nd': 2.89, 'O': 34.69}
			Density None, Hardness None, Elements {'Sr': 1.02, 'Ca': 2.81, 'La': 5.33, 'Ce': 10.75, 'Sm': 0.25, 'Mg': 0.16, 'Ti': 0.8, 'Mn': 20.43, 'Al': 4.5, 'Fe': 2.14, 'Si': 14.05, 'H': 0.17, 'Nd': 2.89, 'O': 34.69}
	Found duplicates of "Manganiandrosite-La", with these properties :
			Density 4.21, Hardness None, Elements {'Ca': 2.02, 'La': 11.67, 'Ce': 4.71, 'Mn': 25.38, 'Al': 4.53, 'Si': 14.16, 'H': 0.17, 'Nd': 2.42, 'O': 34.94}
			Density 4.21, Hardness None, Elements {'Ca': 2.02, 'La': 11.67, 'Ce': 4.71, 'Mn': 25.38, 'Al': 4.53, 'Si': 14.16, 'H': 0.17, 'Nd': 2.42, 'O': 34.94}
			Density 4.21, Hardness None, Elements {'Ca': 2.02, 'La': 11.67, 'Ce': 4.71, 'Mn': 25.38, 'Al': 4.53, 'Si': 14.16, 'H': 0.17, 'Nd': 2.42, 'O': 34.94}
			Density 4.21, Hardness None, Elements {'Ca': 2.02, 'La': 11.67, 'Ce': 4.71, 'Mn': 25.38, 'Al': 4.53, 'Si': 14.16, 'H': 0.17, 'Nd': 2.42, 'O': 34.94}
	Found duplicates of "Manganvesuvianite", with these properties :
			Density None, Hardness 6.5, Elements {'Ca': 25.67, 'Mg': 1.4, 'Mn': 4.84, 'Al': 7.86, 'Fe': 0.76, 'Si': 16.94, 'H': 0.3, 'O': 42.23}
			Density None, Hardness 6.5, Elements {'Ca': 25.67, 'Mg': 1.4, 'Mn': 4.84, 'Al': 7.86, 'Fe': 0.76, 'Si': 16.94, 'H': 0.3, 'O': 42.23}
			Density None, Hardness 6.5, Elements {'Ca': 25.67, 'Mg': 1.4, 'Mn': 4.84, 'Al': 7.86, 'Fe': 0.76, 'Si': 16.94, 'H': 0.3, 'O': 42.23}
			Density None, Hardness 6.5, Elements {'Ca': 25.67, 'Mg': 1.4, 'Mn': 4.84, 'Al': 7.86, 'Fe': 0.76, 'Si': 16.94, 'H': 0.3, 'O': 42.23}
	Found duplicates of "Manganilvaite", with these properties :
			Density 3.92, Hardness 5.75, Elements {'Ca': 9.25, 'Mg': 0.3, 'Mn': 10.39, 'Al': 0.2, 'Fe': 30.45, 'Si': 13.8, 'H': 0.25, 'O': 35.37}
			Density 3.92, Hardness 5.75, Elements {'Ca': 9.25, 'Mg': 0.3, 'Mn': 10.39, 'Al': 0.2, 'Fe': 30.45, 'Si': 13.8, 'H': 0.25, 'O': 35.37}
	Found duplicates of "Manganipiemontite-Sr", with these properties :
			Density None, Hardness 6.5, Elements {'Sr': 15.67, 'Ca': 7.17, 'Mn': 14.74, 'Al': 6.27, 'Fe': 4.0, 'Si': 15.07, 'H': 0.16, 'O': 36.92}
			Density None, Hardness 6.5, Elements {'Sr': 15.67, 'Ca': 7.17, 'Mn': 14.74, 'Al': 6.27, 'Fe': 4.0, 'Si': 15.07, 'H': 0.16, 'O': 36.92}
			Density None, Hardness 6.5, Elements {'Sr': 15.67, 'Ca': 7.17, 'Mn': 14.74, 'Al': 6.27, 'Fe': 4.0, 'Si': 15.07, 'H': 0.16, 'O': 36.92}
	Found duplicates of "Manganlotharmeyerite", with these properties :
			Density 3.77, Hardness 3.0, Elements {'Ca': 8.7, 'Mg': 2.34, 'Mn': 13.24, 'As': 36.12, 'H': 1.02, 'O': 38.57}
			Density 3.77, Hardness 3.0, Elements {'Ca': 8.7, 'Mg': 2.34, 'Mn': 13.24, 'As': 36.12, 'H': 1.02, 'O': 38.57}
	Found duplicates of "Parvo-mangano-edenite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 0.09, 'Na': 2.0, 'Ca': 5.98, 'Mg': 12.87, 'Ti': 0.06, 'Mn': 6.52, 'Al': 3.33, 'Fe': 0.52, 'Si': 23.32, 'H': 0.24, 'O': 45.09}
			Density None, Hardness 6.0, Elements {'K': 0.09, 'Na': 2.0, 'Ca': 5.98, 'Mg': 12.87, 'Ti': 0.06, 'Mn': 6.52, 'Al': 3.33, 'Fe': 0.52, 'Si': 23.32, 'H': 0.24, 'O': 45.09}
			Density None, Hardness 6.0, Elements {'K': 0.09, 'Na': 2.0, 'Ca': 5.98, 'Mg': 12.87, 'Ti': 0.06, 'Mn': 6.52, 'Al': 3.33, 'Fe': 0.52, 'Si': 23.32, 'H': 0.24, 'O': 45.09}
	Found duplicates of "Calcite", with these properties :
			Density 2.71, Hardness 3.0, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
			Density 2.71, Hardness 3.0, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
			Density 2.71, Hardness 3.0, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
			Density 2.71, Hardness 3.0, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
			Density 2.71, Hardness 3.0, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
	Found duplicates of "Columbite-Mn", with these properties :
			Density 5.28, Hardness 6.0, Elements {'Ta': 27.5, 'Ti': 1.21, 'Mn': 6.96, 'Nb': 32.94, 'Fe': 7.07, 'O': 24.31}
			Density 5.28, Hardness 6.0, Elements {'Ta': 27.5, 'Ti': 1.21, 'Mn': 6.96, 'Nb': 32.94, 'Fe': 7.07, 'O': 24.31}
	Found duplicates of "Manganocummingtonite", with these properties :
			Density 3.07, Hardness 6.25, Elements {'Na': 6.77, 'Mg': 11.46, 'Mn': 3.24, 'Fe': 6.58, 'Si': 26.47, 'H': 0.24, 'O': 45.24}
			Density 3.07, Hardness 6.25, Elements {'Na': 6.77, 'Mg': 11.46, 'Mn': 3.24, 'Fe': 6.58, 'Si': 26.47, 'H': 0.24, 'O': 45.24}
	Found duplicates of "Manganogrunerite", with these properties :
			Density 3.25, Hardness 5.5, Elements {'Mn': 10.99, 'Fe': 27.93, 'Si': 22.47, 'H': 0.2, 'O': 38.41}
			Density 3.25, Hardness 5.5, Elements {'Mn': 10.99, 'Fe': 27.93, 'Si': 22.47, 'H': 0.2, 'O': 38.41}
	Found duplicates of "Manganokhomyakovite", with these properties :
			Density 3.13, Hardness 5.5, Elements {'Na': 8.24, 'Sr': 7.85, 'Ca': 7.18, 'Zr': 8.18, 'Mn': 4.92, 'Si': 20.98, 'H': 0.07, 'W': 5.49, 'Cl': 0.53, 'O': 36.55}
			Density 3.13, Hardness 5.5, Elements {'Na': 8.24, 'Sr': 7.85, 'Ca': 7.18, 'Zr': 8.18, 'Mn': 4.92, 'Si': 20.98, 'H': 0.07, 'W': 5.49, 'Cl': 0.53, 'O': 36.55}
	Found duplicates of "Manganokukisvumite", with these properties :
			Density 2.86, Hardness 5.75, Elements {'K': 0.17, 'Na': 11.81, 'Ca': 0.07, 'Ce': 0.12, 'Mg': 0.15, 'Ti': 16.43, 'Mn': 4.32, 'Nb': 0.49, 'Al': 0.1, 'Fe': 0.44, 'Si': 19.89, 'H': 0.71, 'O': 45.28}
			Density 2.86, Hardness 5.75, Elements {'K': 0.17, 'Na': 11.81, 'Ca': 0.07, 'Ce': 0.12, 'Mg': 0.15, 'Ti': 16.43, 'Mn': 4.32, 'Nb': 0.49, 'Al': 0.1, 'Fe': 0.44, 'Si': 19.89, 'H': 0.71, 'O': 45.28}
	Found duplicates of "Manganonaujakasite", with these properties :
			Density 2.67, Hardness 3.5, Elements {'Na': 14.63, 'Mn': 3.09, 'Al': 11.44, 'Fe': 2.9, 'Si': 23.83, 'O': 44.11}
			Density 2.67, Hardness 3.5, Elements {'Na': 14.63, 'Mn': 3.09, 'Al': 11.44, 'Fe': 2.9, 'Si': 23.83, 'O': 44.11}
	Found duplicates of "Manganonordite-Ce", with these properties :
			Density 3.49, Hardness 5.25, Elements {'Na': 8.71, 'Sr': 11.06, 'Ce': 17.69, 'Mn': 6.94, 'Si': 21.27, 'O': 34.34}
			Density 3.49, Hardness 5.25, Elements {'Na': 8.71, 'Sr': 11.06, 'Ce': 17.69, 'Mn': 6.94, 'Si': 21.27, 'O': 34.34}
	Found duplicates of "Wollastonite-2M", with these properties :
			Density 2.84, Hardness 5.0, Elements {'Ca': 34.5, 'Si': 24.18, 'O': 41.32}
			Density 2.84, Hardness 5.0, Elements {'Ca': 34.5, 'Si': 24.18, 'O': 41.32}
			Density 2.84, Hardness 5.0, Elements {'Ca': 34.5, 'Si': 24.18, 'O': 41.32}
	Found duplicates of "Biotite", with these properties :
			Density 3.09, Hardness 2.75, Elements {'K': 9.02, 'Mg': 14.02, 'Al': 6.22, 'Fe': 6.44, 'Si': 19.44, 'H': 0.41, 'O': 43.36, 'F': 1.1}
			Density 3.09, Hardness 2.75, Elements {'K': 9.02, 'Mg': 14.02, 'Al': 6.22, 'Fe': 6.44, 'Si': 19.44, 'H': 0.41, 'O': 43.36, 'F': 1.1}
			Density 3.09, Hardness 2.75, Elements {'K': 9.02, 'Mg': 14.02, 'Al': 6.22, 'Fe': 6.44, 'Si': 19.44, 'H': 0.41, 'O': 43.36, 'F': 1.1}
			Density 3.09, Hardness 2.75, Elements {'K': 9.02, 'Mg': 14.02, 'Al': 6.22, 'Fe': 6.44, 'Si': 19.44, 'H': 0.41, 'O': 43.36, 'F': 1.1}
			Density 3.09, Hardness 2.75, Elements {'K': 9.02, 'Mg': 14.02, 'Al': 6.22, 'Fe': 6.44, 'Si': 19.44, 'H': 0.41, 'O': 43.36, 'F': 1.1}
	Found duplicates of "Manganosegelerite", with these properties :
			Density 2.76, Hardness 3.5, Elements {'Ca': 3.75, 'Mg': 1.71, 'Mn': 12.87, 'Al': 0.63, 'Fe': 15.7, 'P': 14.51, 'H': 2.12, 'O': 48.71}
			Density 2.76, Hardness 3.5, Elements {'Ca': 3.75, 'Mg': 1.71, 'Mn': 12.87, 'Al': 0.63, 'Fe': 15.7, 'P': 14.51, 'H': 2.12, 'O': 48.71}
	Found duplicates of "Tantalite-Mn", with these properties :
			Density 8.1, Hardness 6.25, Elements {'Ta': 70.57, 'Mn': 10.71, 'O': 18.72}
			Density 8.1, Hardness 6.25, Elements {'Ta': 70.57, 'Mn': 10.71, 'O': 18.72}
			Density 8.1, Hardness 6.25, Elements {'Ta': 70.57, 'Mn': 10.71, 'O': 18.72}
	Found duplicates of "Tapiolite-Mn", with these properties :
			Density 7.72, Hardness 6.0, Elements {'Ca': 0.33, 'Ta': 63.73, 'Mn': 7.97, 'Nb': 5.77, 'Fe': 2.31, 'O': 19.89}
			Density 7.72, Hardness 6.0, Elements {'Ca': 0.33, 'Ta': 63.73, 'Mn': 7.97, 'Nb': 5.77, 'Fe': 2.31, 'O': 19.89}
	Found duplicates of "Manganotychite", with these properties :
			Density 2.7, Hardness 4.0, Elements {'Na': 23.85, 'Mg': 0.84, 'Mn': 11.4, 'Fe': 5.79, 'C': 8.31, 'S': 5.54, 'O': 44.26}
			Density 2.7, Hardness 4.0, Elements {'Na': 23.85, 'Mg': 0.84, 'Mn': 11.4, 'Fe': 5.79, 'C': 8.31, 'S': 5.54, 'O': 44.26}
	Found duplicates of "Pyrosmalite-Mn", with these properties :
			Density 3.12, Hardness 4.5, Elements {'Mn': 29.74, 'Fe': 9.38, 'Si': 15.73, 'H': 0.56, 'Cl': 13.23, 'O': 31.36}
			Density 3.12, Hardness 4.5, Elements {'Mn': 29.74, 'Fe': 9.38, 'Si': 15.73, 'H': 0.56, 'Cl': 13.23, 'O': 31.36}
	Found duplicates of "Mangazeite", with these properties :
			Density None, Hardness 1.0, Elements {'Al': 19.28, 'H': 3.87, 'S': 11.63, 'O': 65.22}
			Density None, Hardness 1.0, Elements {'Al': 19.28, 'H': 3.87, 'S': 11.63, 'O': 65.22}
	Found duplicates of "Mannardite", with these properties :
			Density 4.27, Hardness 7.0, Elements {'Ba': 17.16, 'Ti': 35.89, 'V': 12.73, 'H': 0.25, 'O': 33.98}
			Density 4.27, Hardness 7.0, Elements {'Ba': 17.16, 'Ti': 35.89, 'V': 12.73, 'H': 0.25, 'O': 33.98}
	Found duplicates of "Maoniupingite-Ce", with these properties :
			Density None, Hardness None, Elements {'Ca': 3.26, 'RE': 35.15, 'Ti': 8.96, 'Nb': 3.02, 'Fe': 11.82, 'Si': 9.14, 'O': 28.64}
			Density None, Hardness None, Elements {'Ca': 3.26, 'RE': 35.15, 'Ti': 8.96, 'Nb': 3.02, 'Fe': 11.82, 'Si': 9.14, 'O': 28.64}
	Found duplicates of "Marecottite", with these properties :
			Density 4.03, Hardness 3.0, Elements {'Mg': 1.31, 'U': 59.24, 'Mn': 0.96, 'H': 1.77, 'S': 3.85, 'O': 32.87}
			Density 4.03, Hardness 3.0, Elements {'Mg': 1.31, 'U': 59.24, 'Mn': 0.96, 'H': 1.77, 'S': 3.85, 'O': 32.87}
	Found duplicates of "Marianoite", with these properties :
			Density 3.32, Hardness 6.0, Elements {'Na': 5.6, 'Ca': 20.21, 'Hf': 0.14, 'Mg': 0.09, 'Zr': 10.35, 'Ta': 0.07, 'Ti': 0.57, 'Mn': 0.29, 'Nb': 11.34, 'Fe': 0.56, 'Si': 14.05, 'O': 34.17, 'F': 2.57}
			Density 3.32, Hardness 6.0, Elements {'Na': 5.6, 'Ca': 20.21, 'Hf': 0.14, 'Mg': 0.09, 'Zr': 10.35, 'Ta': 0.07, 'Ti': 0.57, 'Mn': 0.29, 'Nb': 11.34, 'Fe': 0.56, 'Si': 14.05, 'O': 34.17, 'F': 2.57}
	Found duplicates of "Maricite", with these properties :
			Density 3.66, Hardness 4.25, Elements {'Na': 13.23, 'Fe': 32.13, 'P': 17.82, 'O': 36.82}
			Density 3.66, Hardness 4.25, Elements {'Na': 13.23, 'Fe': 32.13, 'P': 17.82, 'O': 36.82}
	Found duplicates of "Ceriopyrochlore-Ce", with these properties :
			Density 4.13, Hardness 5.25, Elements {'Ca': 4.29, 'Ce': 30.0, 'Y': 3.17, 'Zr': 1.63, 'Ta': 25.83, 'Nb': 13.26, 'Fe': 1.0, 'H': 0.13, 'O': 19.84, 'F': 0.85}
			Density 4.13, Hardness 5.25, Elements {'Ca': 4.29, 'Ce': 30.0, 'Y': 3.17, 'Zr': 1.63, 'Ta': 25.83, 'Nb': 13.26, 'Fe': 1.0, 'H': 0.13, 'O': 19.84, 'F': 0.85}
	Found duplicates of "Marinellite", with these properties :
			Density 2.405, Hardness 5.5, Elements {'K': 6.59, 'Na': 11.09, 'Ca': 3.68, 'Al': 14.71, 'Si': 15.3, 'H': 0.1, 'S': 3.94, 'Cl': 0.87, 'O': 43.72}
			Density 2.405, Hardness 5.5, Elements {'K': 6.59, 'Na': 11.09, 'Ca': 3.68, 'Al': 14.71, 'Si': 15.3, 'H': 0.1, 'S': 3.94, 'Cl': 0.87, 'O': 43.72}
	Found duplicates of "Hydrozincite", with these properties :
			Density 3.5, Hardness 2.25, Elements {'Zn': 59.55, 'H': 1.1, 'C': 4.38, 'O': 34.97}
			Density 3.5, Hardness 2.25, Elements {'Zn': 59.55, 'H': 1.1, 'C': 4.38, 'O': 34.97}
	Found duplicates of "Marrucciite", with these properties :
			Density None, Hardness None, Elements {'Cu': 0.18, 'Hg': 7.93, 'Sb': 29.8, 'Pb': 42.51, 'S': 19.52, 'Cl': 0.06}
			Density None, Hardness None, Elements {'Cu': 0.18, 'Hg': 7.93, 'Sb': 29.8, 'Pb': 42.51, 'S': 19.52, 'Cl': 0.06}
			Density None, Hardness None, Elements {'Cu': 0.18, 'Hg': 7.93, 'Sb': 29.8, 'Pb': 42.51, 'S': 19.52, 'Cl': 0.06}
			Density None, Hardness None, Elements {'Cu': 0.18, 'Hg': 7.93, 'Sb': 29.8, 'Pb': 42.51, 'S': 19.52, 'Cl': 0.06}
	Found duplicates of "Martinite", with these properties :
			Density None, Hardness 4.0, Elements {'Na': 13.29, 'Ca': 12.08, 'Mg': 0.02, 'Ti': 0.03, 'Mn': 0.07, 'Si': 23.12, 'B': 2.58, 'H': 0.61, 'S': 0.93, 'Cl': 1.12, 'O': 43.95, 'F': 2.2}
			Density None, Hardness 4.0, Elements {'Na': 13.29, 'Ca': 12.08, 'Mg': 0.02, 'Ti': 0.03, 'Mn': 0.07, 'Si': 23.12, 'B': 2.58, 'H': 0.61, 'S': 0.93, 'Cl': 1.12, 'O': 43.95, 'F': 2.2}
	Found duplicates of "Hematite", with these properties :
			Density 5.3, Hardness 6.5, Elements {'Fe': 69.94, 'O': 30.06}
			Density 5.3, Hardness 6.5, Elements {'Fe': 69.94, 'O': 30.06}
			Density 5.3, Hardness 6.5, Elements {'Fe': 69.94, 'O': 30.06}
			Density 5.3, Hardness 6.5, Elements {'Fe': 69.94, 'O': 30.06}
	Found duplicates of "Berthierite", with these properties :
			Density 4.3, Hardness 2.25, Elements {'Fe': 13.06, 'Sb': 56.94, 'S': 30.0}
			Density 4.3, Hardness 2.25, Elements {'Fe': 13.06, 'Sb': 56.94, 'S': 30.0}
			Density 4.3, Hardness 2.25, Elements {'Fe': 13.06, 'Sb': 56.94, 'S': 30.0}
			Density 4.3, Hardness 2.25, Elements {'Fe': 13.06, 'Sb': 56.94, 'S': 30.0}
	Found duplicates of "Martyite", with these properties :
			Density 3.37, Hardness 3.0, Elements {'Ca': 0.43, 'V': 21.77, 'Zn': 37.16, 'Co': 1.89, 'H': 1.33, 'O': 37.43}
			Density 3.37, Hardness 3.0, Elements {'Ca': 0.43, 'V': 21.77, 'Zn': 37.16, 'Co': 1.89, 'H': 1.33, 'O': 37.43}
	Found duplicates of "Marumoite", with these properties :
			Density None, Hardness None, Elements {'As': 23.83, 'Pb': 52.72, 'S': 23.46}
			Density None, Hardness None, Elements {'As': 23.83, 'Pb': 52.72, 'S': 23.46}
	Found duplicates of "Matioliite", with these properties :
			Density None, Hardness 5.0, Elements {'Na': 2.75, 'Ca': 0.05, 'Mg': 2.73, 'Mn': 0.07, 'Al': 16.64, 'Fe': 1.71, 'P': 15.91, 'H': 1.74, 'O': 58.4}
			Density None, Hardness 5.0, Elements {'Na': 2.75, 'Ca': 0.05, 'Mg': 2.73, 'Mn': 0.07, 'Al': 16.64, 'Fe': 1.71, 'P': 15.91, 'H': 1.74, 'O': 58.4}
	Found duplicates of "Matsubaraite", with these properties :
			Density None, Hardness 5.5, Elements {'Sr': 33.25, 'Ti': 22.71, 'Si': 10.66, 'O': 33.39}
			Density None, Hardness 5.5, Elements {'Sr': 33.25, 'Ti': 22.71, 'Si': 10.66, 'O': 33.39}
	Found duplicates of "Mattagamite", with these properties :
			Density None, Hardness 5.5, Elements {'Co': 18.76, 'Te': 81.24}
			Density None, Hardness 5.5, Elements {'Co': 18.76, 'Te': 81.24}
	Found duplicates of "Maucherite", with these properties :
			Density 7.83, Hardness 5.0, Elements {'Ni': 51.86, 'As': 48.14}
			Density 7.83, Hardness 5.0, Elements {'Ni': 51.86, 'As': 48.14}
			Density 7.83, Hardness 5.0, Elements {'Ni': 51.86, 'As': 48.14}
	Found duplicates of "Mavlyanovite", with these properties :
			Density None, Hardness None, Elements {'Mn': 76.53, 'Si': 23.47}
			Density None, Hardness None, Elements {'Mn': 76.53, 'Si': 23.47}
	Found duplicates of "Maxwellite", with these properties :
			Density 3.9, Hardness 5.25, Elements {'Na': 9.71, 'Fe': 23.59, 'As': 31.65, 'O': 27.03, 'F': 8.02}
			Density 3.9, Hardness 5.25, Elements {'Na': 9.71, 'Fe': 23.59, 'As': 31.65, 'O': 27.03, 'F': 8.02}
	Found duplicates of "Majakite", with these properties :
			Density 9.33, Hardness 6.0, Elements {'Ni': 24.45, 'As': 31.21, 'Pd': 44.34}
			Density 9.33, Hardness 6.0, Elements {'Ni': 24.45, 'As': 31.21, 'Pd': 44.34}
	Found duplicates of "Mayenite", with these properties :
			Density 2.85, Hardness None, Elements {'Ca': 34.68, 'Al': 27.24, 'O': 38.08}
			Density 2.85, Hardness None, Elements {'Ca': 34.68, 'Al': 27.24, 'O': 38.08}
	Found duplicates of "Mayingite", with these properties :
			Density 12.72, Hardness 4.0, Elements {'Bi': 39.52, 'Te': 24.13, 'Ir': 36.35}
			Density 12.72, Hardness 4.0, Elements {'Bi': 39.52, 'Te': 24.13, 'Ir': 36.35}
	Found duplicates of "Mazzettiite", with these properties :
			Density None, Hardness 3.25, Elements {'Ag': 21.59, 'Hg': 13.56, 'Sb': 8.07, 'Te': 42.63, 'Pb': 14.15}
			Density None, Hardness 3.25, Elements {'Ag': 21.59, 'Hg': 13.56, 'Sb': 8.07, 'Te': 42.63, 'Pb': 14.15}
	Found duplicates of "Mazzite-Na", with these properties :
			Density None, Hardness None, Elements {'K': 0.03, 'Ba': 0.14, 'Na': 5.99, 'Ca': 0.12, 'Mg': 0.13, 'Al': 7.59, 'Fe': 0.46, 'Si': 26.94, 'H': 2.09, 'O': 56.49}
			Density None, Hardness None, Elements {'K': 0.03, 'Ba': 0.14, 'Na': 5.99, 'Ca': 0.12, 'Mg': 0.13, 'Al': 7.59, 'Fe': 0.46, 'Si': 26.94, 'H': 2.09, 'O': 56.49}
			Density None, Hardness None, Elements {'K': 0.03, 'Ba': 0.14, 'Na': 5.99, 'Ca': 0.12, 'Mg': 0.13, 'Al': 7.59, 'Fe': 0.46, 'Si': 26.94, 'H': 2.09, 'O': 56.49}
	Found duplicates of "Taramite", with these properties :
			Density 3.5, Hardness 5.5, Elements {'Na': 4.87, 'Ca': 4.24, 'Al': 8.57, 'Fe': 23.64, 'Si': 17.83, 'H': 0.21, 'O': 40.64}
			Density 3.5, Hardness 5.5, Elements {'Na': 4.87, 'Ca': 4.24, 'Al': 8.57, 'Fe': 23.64, 'Si': 17.83, 'H': 0.21, 'O': 40.64}
	Found duplicates of "Mcalpineite", with these properties :
			Density 6.63, Hardness 3.0, Elements {'Cu': 44.1, 'Te': 29.52, 'H': 0.47, 'O': 25.91}
			Density 6.63, Hardness 3.0, Elements {'Cu': 44.1, 'Te': 29.52, 'H': 0.47, 'O': 25.91}
	Found duplicates of "Mcauslanite", with these properties :
			Density 2.22, Hardness 3.5, Elements {'Al': 5.71, 'Fe': 17.72, 'P': 13.1, 'H': 3.94, 'O': 57.52, 'F': 2.01}
			Density 2.22, Hardness 3.5, Elements {'Al': 5.71, 'Fe': 17.72, 'P': 13.1, 'H': 3.94, 'O': 57.52, 'F': 2.01}
	Found duplicates of "Mccrillisite", with these properties :
			Density 3.12, Hardness 4.5, Elements {'Cs': 17.63, 'Na': 3.05, 'Li': 0.23, 'Zr': 24.21, 'Be': 0.9, 'P': 16.44, 'H': 0.4, 'O': 37.15}
			Density 3.12, Hardness 4.5, Elements {'Cs': 17.63, 'Na': 3.05, 'Li': 0.23, 'Zr': 24.21, 'Be': 0.9, 'P': 16.44, 'H': 0.4, 'O': 37.15}
	Found duplicates of "Mcgillite", with these properties :
			Density 2.98, Hardness 5.0, Elements {'Mg': 0.47, 'Mn': 37.03, 'Fe': 3.23, 'Si': 16.23, 'H': 0.78, 'Cl': 6.83, 'O': 35.44}
			Density 2.98, Hardness 5.0, Elements {'Mg': 0.47, 'Mn': 37.03, 'Fe': 3.23, 'Si': 16.23, 'H': 0.78, 'Cl': 6.83, 'O': 35.44}
	Found duplicates of "Mckinstryite", with these properties :
			Density 6.61, Hardness 2.0, Elements {'Cu': 23.94, 'Ag': 60.96, 'S': 15.1}
			Density 6.61, Hardness 2.0, Elements {'Cu': 23.94, 'Ag': 60.96, 'S': 15.1}
	Found duplicates of "Medenbachite", with these properties :
			Density 5.9, Hardness 4.5, Elements {'Fe': 7.93, 'Cu': 5.42, 'Bi': 47.51, 'As': 17.03, 'H': 0.29, 'O': 21.82}
			Density 5.9, Hardness 4.5, Elements {'Fe': 7.93, 'Cu': 5.42, 'Bi': 47.51, 'As': 17.03, 'H': 0.29, 'O': 21.82}
	Found duplicates of "Sepiolite", with these properties :
			Density 2.0, Hardness 2.0, Elements {'Mg': 15.84, 'Si': 27.45, 'H': 1.97, 'O': 54.74}
			Density 2.0, Hardness 2.0, Elements {'Mg': 15.84, 'Si': 27.45, 'H': 1.97, 'O': 54.74}
	Found duplicates of "Megacyclite", with these properties :
			Density 1.82, Hardness 2.0, Elements {'K': 3.13, 'Na': 14.71, 'Si': 20.22, 'H': 3.71, 'O': 58.23}
			Density 1.82, Hardness 2.0, Elements {'K': 3.13, 'Na': 14.71, 'Si': 20.22, 'H': 3.71, 'O': 58.23}
	Found duplicates of "Megakalsilite", with these properties :
			Density 2.58, Hardness 6.0, Elements {'K': 24.72, 'Al': 17.06, 'Si': 17.76, 'O': 40.46}
			Density 2.58, Hardness 6.0, Elements {'K': 24.72, 'Al': 17.06, 'Si': 17.76, 'O': 40.46}
	Found duplicates of "Tenorite", with these properties :
			Density 6.5, Hardness 3.75, Elements {'Cu': 79.89, 'O': 20.11}
			Density 6.5, Hardness 3.75, Elements {'Cu': 79.89, 'O': 20.11}
			Density 6.5, Hardness 3.75, Elements {'Cu': 79.89, 'O': 20.11}
			Density 6.5, Hardness 3.75, Elements {'Cu': 79.89, 'O': 20.11}
	Found duplicates of "Andradite", with these properties :
			Density 3.9, Hardness 6.75, Elements {'Ca': 21.01, 'Fe': 19.52, 'Si': 14.73, 'O': 44.74}
			Density 3.9, Hardness 6.75, Elements {'Ca': 21.01, 'Fe': 19.52, 'Si': 14.73, 'O': 44.74}
			Density 3.9, Hardness 6.75, Elements {'Ca': 21.01, 'Fe': 19.52, 'Si': 14.73, 'O': 44.74}
			Density 3.9, Hardness 6.75, Elements {'Ca': 21.01, 'Fe': 19.52, 'Si': 14.73, 'O': 44.74}
	Found duplicates of "Melanophlogite", with these properties :
			Density 2.04, Hardness 6.75, Elements {'Si': 44.11, 'H': 0.78, 'C': 0.82, 'S': 0.55, 'O': 53.75}
			Density 2.04, Hardness 6.75, Elements {'Si': 44.11, 'H': 0.78, 'C': 0.82, 'S': 0.55, 'O': 53.75}
	Found duplicates of "Melanterite", with these properties :
			Density 1.89, Hardness 2.0, Elements {'Fe': 20.09, 'H': 5.08, 'S': 11.53, 'O': 63.3}
			Density 1.89, Hardness 2.0, Elements {'Fe': 20.09, 'H': 5.08, 'S': 11.53, 'O': 63.3}
			Density 1.89, Hardness 2.0, Elements {'Fe': 20.09, 'H': 5.08, 'S': 11.53, 'O': 63.3}
	Found duplicates of "Meliphanite", with these properties :
			Density 3.01, Hardness 5.25, Elements {'Na': 9.53, 'Ca': 16.62, 'Be': 3.74, 'Al': 11.19, 'Si': 11.65, 'H': 0.21, 'O': 43.13, 'F': 3.94}
			Density 3.01, Hardness 5.25, Elements {'Na': 9.53, 'Ca': 16.62, 'Be': 3.74, 'Al': 11.19, 'Si': 11.65, 'H': 0.21, 'O': 43.13, 'F': 3.94}
	Found duplicates of "Melliniite", with these properties :
			Density None, Hardness 8.25, Elements {'Fe': 35.27, 'Co': 0.23, 'Ni': 51.98, 'P': 12.52}
			Density None, Hardness 8.25, Elements {'Fe': 35.27, 'Co': 0.23, 'Ni': 51.98, 'P': 12.52}
	Found duplicates of "Greigite", with these properties :
			Density 4.049, Hardness 4.25, Elements {'Fe': 56.64, 'S': 43.36}
			Density 4.049, Hardness 4.25, Elements {'Fe': 56.64, 'S': 43.36}
	Found duplicates of "Ilmenite", with these properties :
			Density 4.72, Hardness 5.25, Elements {'Ti': 31.56, 'Fe': 36.81, 'O': 31.63}
			Density 4.72, Hardness 5.25, Elements {'Ti': 31.56, 'Fe': 36.81, 'O': 31.63}
			Density 4.72, Hardness 5.25, Elements {'Ti': 31.56, 'Fe': 36.81, 'O': 31.63}
			Density 4.72, Hardness 5.25, Elements {'Ti': 31.56, 'Fe': 36.81, 'O': 31.63}
			Density 4.72, Hardness 5.25, Elements {'Ti': 31.56, 'Fe': 36.81, 'O': 31.63}
			Density 4.72, Hardness 5.25, Elements {'Ti': 31.56, 'Fe': 36.81, 'O': 31.63}
	Found duplicates of "Betafite", with these properties :
			Density 4.3, Hardness 5.25, Elements {'Ca': 1.93, 'U': 17.2, 'Ta': 21.79, 'Ti': 9.23, 'Nb': 20.14, 'Al': 0.65, 'Fe': 1.35, 'H': 0.73, 'O': 26.98}
			Density 4.3, Hardness 5.25, Elements {'Ca': 1.93, 'U': 17.2, 'Ta': 21.79, 'Ti': 9.23, 'Nb': 20.14, 'Al': 0.65, 'Fe': 1.35, 'H': 0.73, 'O': 26.98}
			Density 4.3, Hardness 5.25, Elements {'Ca': 1.93, 'U': 17.2, 'Ta': 21.79, 'Ti': 9.23, 'Nb': 20.14, 'Al': 0.65, 'Fe': 1.35, 'H': 0.73, 'O': 26.98}
			Density 4.3, Hardness 5.25, Elements {'Ca': 1.93, 'U': 17.2, 'Ta': 21.79, 'Ti': 9.23, 'Nb': 20.14, 'Al': 0.65, 'Fe': 1.35, 'H': 0.73, 'O': 26.98}
	Found duplicates of "Menezesite", with these properties :
			Density None, Hardness 4.0, Elements {'K': 0.75, 'Ba': 10.82, 'Na': 0.05, 'Ca': 0.45, 'La': 0.1, 'Ce': 0.86, 'Th': 4.59, 'Mg': 0.82, 'Zr': 9.03, 'U': 0.17, 'Ta': 2.34, 'Ti': 5.6, 'Mn': 0.45, 'Nb': 30.86, 'Al': 0.03, 'Fe': 0.46, 'Si': 0.12, 'H': 0.87, 'Nd': 0.52, 'O': 31.09}
			Density None, Hardness 4.0, Elements {'K': 0.75, 'Ba': 10.82, 'Na': 0.05, 'Ca': 0.45, 'La': 0.1, 'Ce': 0.86, 'Th': 4.59, 'Mg': 0.82, 'Zr': 9.03, 'U': 0.17, 'Ta': 2.34, 'Ti': 5.6, 'Mn': 0.45, 'Nb': 30.86, 'Al': 0.03, 'Fe': 0.46, 'Si': 0.12, 'H': 0.87, 'Nd': 0.52, 'O': 31.09}
	Found duplicates of "Meniaylovite", with these properties :
			Density None, Hardness None, Elements {'Ca': 20.7, 'Al': 3.48, 'Si': 3.63, 'H': 3.12, 'S': 4.14, 'O': 33.05, 'F': 31.88}
			Density None, Hardness None, Elements {'Ca': 20.7, 'Al': 3.48, 'Si': 3.63, 'H': 3.12, 'S': 4.14, 'O': 33.05, 'F': 31.88}
	Found duplicates of "Menshikovite", with these properties :
			Density None, Hardness 5.0, Elements {'Ni': 17.75, 'As': 33.98, 'Pd': 48.27}
			Density None, Hardness 5.0, Elements {'Ni': 17.75, 'As': 33.98, 'Pd': 48.27}
	Found duplicates of "Mercury", with these properties :
			Density 13.6, Hardness 0.0, Elements {'Hg': 100.0}
			Density 13.6, Hardness 0.0, Elements {'Hg': 100.0}
			Density 13.6, Hardness 0.0, Elements {'Hg': 100.0}
	Found duplicates of "Mereheadite", with these properties :
			Density 7.4, Hardness 3.5, Elements {'H': 0.21, 'Pb': 85.82, 'Cl': 7.34, 'O': 6.63}
			Density 7.4, Hardness 3.5, Elements {'H': 0.21, 'Pb': 85.82, 'Cl': 7.34, 'O': 6.63}
	Found duplicates of "Mereiterite", with these properties :
			Density 2.36, Hardness 2.75, Elements {'K': 19.64, 'Fe': 14.02, 'H': 2.02, 'S': 16.1, 'O': 48.21}
			Density 2.36, Hardness 2.75, Elements {'K': 19.64, 'Fe': 14.02, 'H': 2.02, 'S': 16.1, 'O': 48.21}
	Found duplicates of "Meridianiite", with these properties :
			Density None, Hardness None, Elements {'Mg': 7.63, 'H': 6.96, 'S': 10.07, 'O': 75.34}
			Density None, Hardness None, Elements {'Mg': 7.63, 'H': 6.96, 'S': 10.07, 'O': 75.34}
			Density None, Hardness None, Elements {'Mg': 7.63, 'H': 6.96, 'S': 10.07, 'O': 75.34}
	Found duplicates of "Merrillite", with these properties :
			Density None, Hardness None, Elements {'Na': 2.14, 'Ca': 33.62, 'Mg': 2.27, 'P': 20.21, 'O': 41.76}
			Density None, Hardness None, Elements {'Na': 2.14, 'Ca': 33.62, 'Mg': 2.27, 'P': 20.21, 'O': 41.76}
			Density None, Hardness None, Elements {'Na': 2.14, 'Ca': 33.62, 'Mg': 2.27, 'P': 20.21, 'O': 41.76}
	Found duplicates of "Ferromerrillite", with these properties :
			Density None, Hardness None, Elements {'Na': 1.09, 'Ca': 33.29, 'Mg': 0.44, 'Mn': 0.35, 'Fe': 4.13, 'P': 19.79, 'O': 40.9}
			Density None, Hardness None, Elements {'Na': 1.09, 'Ca': 33.29, 'Mg': 0.44, 'Mn': 0.35, 'Fe': 4.13, 'P': 19.79, 'O': 40.9}
			Density None, Hardness None, Elements {'Na': 1.09, 'Ca': 33.29, 'Mg': 0.44, 'Mn': 0.35, 'Fe': 4.13, 'P': 19.79, 'O': 40.9}
	Found duplicates of "Mesolite", with these properties :
			Density 2.29, Hardness 5.0, Elements {'Na': 3.95, 'Ca': 6.88, 'Al': 13.9, 'Si': 21.7, 'H': 1.38, 'O': 52.19}
			Density 2.29, Hardness 5.0, Elements {'Na': 3.95, 'Ca': 6.88, 'Al': 13.9, 'Si': 21.7, 'H': 1.38, 'O': 52.19}
	Found duplicates of "Natrolite", with these properties :
			Density 2.25, Hardness 5.75, Elements {'Na': 12.09, 'Al': 14.19, 'Si': 22.16, 'H': 1.06, 'O': 50.49}
			Density 2.25, Hardness 5.75, Elements {'Na': 12.09, 'Al': 14.19, 'Si': 22.16, 'H': 1.06, 'O': 50.49}
			Density 2.25, Hardness 5.75, Elements {'Na': 12.09, 'Al': 14.19, 'Si': 22.16, 'H': 1.06, 'O': 50.49}
			Density 2.25, Hardness 5.75, Elements {'Na': 12.09, 'Al': 14.19, 'Si': 22.16, 'H': 1.06, 'O': 50.49}
			Density 2.25, Hardness 5.75, Elements {'Na': 12.09, 'Al': 14.19, 'Si': 22.16, 'H': 1.06, 'O': 50.49}
	Found duplicates of "Brass", with these properties :
			Density None, Hardness None, Elements {'Zn': 40.69, 'Cu': 59.31}
			Density None, Hardness None, Elements {'Zn': 40.69, 'Cu': 59.31}
			Density None, Hardness None, Elements {'Zn': 40.69, 'Cu': 59.31}
	Found duplicates of "Meta-ankoleite", with these properties :
			Density 3.54, Hardness 2.25, Elements {'K': 8.53, 'U': 51.96, 'P': 6.76, 'H': 1.32, 'O': 31.43}
			Density 3.54, Hardness 2.25, Elements {'K': 8.53, 'U': 51.96, 'P': 6.76, 'H': 1.32, 'O': 31.43}
			Density 3.54, Hardness 2.25, Elements {'K': 8.53, 'U': 51.96, 'P': 6.76, 'H': 1.32, 'O': 31.43}
	Found duplicates of "Meta-autunite", with these properties :
			Density 3.5, Hardness 1.0, Elements {'Ca': 4.76, 'U': 56.53, 'P': 7.36, 'H': 0.96, 'O': 30.4}
			Density 3.5, Hardness 1.0, Elements {'Ca': 4.76, 'U': 56.53, 'P': 7.36, 'H': 0.96, 'O': 30.4}
	Found duplicates of "Metalodevite", with these properties :
			Density 4.0, Hardness 2.25, Elements {'U': 44.77, 'Zn': 6.15, 'As': 14.09, 'H': 1.9, 'O': 33.1}
			Density 4.0, Hardness 2.25, Elements {'U': 44.77, 'Zn': 6.15, 'As': 14.09, 'H': 1.9, 'O': 33.1}
	Found duplicates of "Natrouranospinite", with these properties :
			Density 3.846, Hardness 2.5, Elements {'Na': 3.62, 'Ca': 1.05, 'U': 49.98, 'As': 15.73, 'H': 1.06, 'O': 28.56}
			Density 3.846, Hardness 2.5, Elements {'Na': 3.62, 'Ca': 1.05, 'U': 49.98, 'As': 15.73, 'H': 1.06, 'O': 28.56}
			Density 3.846, Hardness 2.5, Elements {'Na': 3.62, 'Ca': 1.05, 'U': 49.98, 'As': 15.73, 'H': 1.06, 'O': 28.56}
			Density 3.846, Hardness 2.5, Elements {'Na': 3.62, 'Ca': 1.05, 'U': 49.98, 'As': 15.73, 'H': 1.06, 'O': 28.56}
	Found duplicates of "Metauranocircite", with these properties :
			Density 3.95, Hardness 2.25, Elements {'Ba': 13.82, 'U': 47.92, 'P': 6.24, 'H': 1.42, 'O': 30.6}
			Density 3.95, Hardness 2.25, Elements {'Ba': 13.82, 'U': 47.92, 'P': 6.24, 'H': 1.42, 'O': 30.6}
	Found duplicates of "Metauranopilite", with these properties :
			Density None, Hardness None, Elements {'U': 72.26, 'H': 1.02, 'S': 1.62, 'O': 25.1}
			Density None, Hardness None, Elements {'U': 72.26, 'H': 1.02, 'S': 1.62, 'O': 25.1}
	Found duplicates of "Metauranospinite", with these properties :
			Density None, Hardness 2.5, Elements {'Ca': 4.0, 'U': 47.51, 'As': 14.95, 'H': 1.61, 'O': 31.93}
			Density None, Hardness 2.5, Elements {'Ca': 4.0, 'U': 47.51, 'As': 14.95, 'H': 1.61, 'O': 31.93}
	Found duplicates of "Beryllite", with these properties :
			Density 2.196, Hardness 1.0, Elements {'Be': 15.8, 'Si': 16.41, 'H': 2.36, 'O': 65.44}
			Density 2.196, Hardness 1.0, Elements {'Be': 15.8, 'Si': 16.41, 'H': 2.36, 'O': 65.44}
	Found duplicates of "Metacinnabar", with these properties :
			Density 7.75, Hardness 3.0, Elements {'Hg': 86.22, 'S': 13.78}
			Density 7.75, Hardness 3.0, Elements {'Hg': 86.22, 'S': 13.78}
			Density 7.75, Hardness 3.0, Elements {'Hg': 86.22, 'S': 13.78}
			Density 7.75, Hardness 3.0, Elements {'Hg': 86.22, 'S': 13.78}
			Density 7.75, Hardness 3.0, Elements {'Hg': 86.22, 'S': 13.78}
	Found duplicates of "Metakottigite", with these properties :
			Density None, Hardness 2.0, Elements {'Zn': 18.41, 'Fe': 12.03, 'As': 24.82, 'H': 2.34, 'O': 42.4}
			Density None, Hardness 2.0, Elements {'Zn': 18.41, 'Fe': 12.03, 'As': 24.82, 'H': 2.34, 'O': 42.4}
	Found duplicates of "Metamunirite", with these properties :
			Density None, Hardness 1.0, Elements {'Na': 18.85, 'V': 41.78, 'O': 39.37}
			Density None, Hardness 1.0, Elements {'Na': 18.85, 'V': 41.78, 'O': 39.37}
	Found duplicates of "Metarauchite", with these properties :
			Density None, Hardness None, Elements {'U': 46.64, 'Ni': 5.75, 'As': 14.68, 'H': 1.58, 'O': 31.35}
			Density None, Hardness None, Elements {'U': 46.64, 'Ni': 5.75, 'As': 14.68, 'H': 1.58, 'O': 31.35}
	Found duplicates of "Phosphosiderite", with these properties :
			Density 2.76, Hardness 3.75, Elements {'Fe': 29.89, 'P': 16.58, 'H': 2.16, 'O': 51.38}
			Density 2.76, Hardness 3.75, Elements {'Fe': 29.89, 'P': 16.58, 'H': 2.16, 'O': 51.38}
	Found duplicates of "Metavivianite", with these properties :
			Density 2.69, Hardness 1.75, Elements {'Fe': 33.43, 'P': 12.36, 'H': 3.12, 'O': 51.09}
			Density 2.69, Hardness 1.75, Elements {'Fe': 33.43, 'P': 12.36, 'H': 3.12, 'O': 51.09}
			Density 2.69, Hardness 1.75, Elements {'Fe': 33.43, 'P': 12.36, 'H': 3.12, 'O': 51.09}
			Density 2.69, Hardness 1.75, Elements {'Fe': 33.43, 'P': 12.36, 'H': 3.12, 'O': 51.09}
	Found duplicates of "Methane hydrate-II", with these properties :
			Density 0.95, Hardness 2.5, Elements {'H': 13.13, 'C': 13.04, 'O': 73.83}
			Density 0.95, Hardness 2.5, Elements {'H': 13.13, 'C': 13.04, 'O': 73.83}
			Density 0.95, Hardness 2.5, Elements {'H': 13.13, 'C': 13.04, 'O': 73.83}
	Found duplicates of "Meurigite-K", with these properties :
			Density 2.96, Hardness 3.0, Elements {'K': 2.83, 'Na': 0.05, 'Al': 0.36, 'Fe': 33.37, 'Cu': 0.15, 'P': 13.49, 'H': 1.82, 'C': 0.2, 'O': 47.72}
			Density 2.96, Hardness 3.0, Elements {'K': 2.83, 'Na': 0.05, 'Al': 0.36, 'Fe': 33.37, 'Cu': 0.15, 'P': 13.49, 'H': 1.82, 'C': 0.2, 'O': 47.72}
			Density 2.96, Hardness 3.0, Elements {'K': 2.83, 'Na': 0.05, 'Al': 0.36, 'Fe': 33.37, 'Cu': 0.15, 'P': 13.49, 'H': 1.82, 'C': 0.2, 'O': 47.72}
	Found duplicates of "Meurigite-Na", with these properties :
			Density 2.94, Hardness 3.0, Elements {'K': 0.28, 'Na': 1.6, 'Ca': 0.16, 'Mg': 0.02, 'Al': 2.87, 'V': 0.49, 'Fe': 29.88, 'Cu': 0.21, 'P': 14.4, 'H': 1.68, 'O': 48.42}
			Density 2.94, Hardness 3.0, Elements {'K': 0.28, 'Na': 1.6, 'Ca': 0.16, 'Mg': 0.02, 'Al': 2.87, 'V': 0.49, 'Fe': 29.88, 'Cu': 0.21, 'P': 14.4, 'H': 1.68, 'O': 48.42}
	Found duplicates of "Magnesiozippeite", with these properties :
			Density 3.3, Hardness 5.25, Elements {'Mg': 3.22, 'U': 63.01, 'H': 0.93, 'S': 4.24, 'O': 28.59}
			Density 3.3, Hardness 5.25, Elements {'Mg': 3.22, 'U': 63.01, 'H': 0.93, 'S': 4.24, 'O': 28.59}
			Density 3.3, Hardness 5.25, Elements {'Mg': 3.22, 'U': 63.01, 'H': 0.93, 'S': 4.24, 'O': 28.59}
			Density 3.3, Hardness 5.25, Elements {'Mg': 3.22, 'U': 63.01, 'H': 0.93, 'S': 4.24, 'O': 28.59}
			Density 3.3, Hardness 5.25, Elements {'Mg': 3.22, 'U': 63.01, 'H': 0.93, 'S': 4.24, 'O': 28.59}
	Found duplicates of "Miassite", with these properties :
			Density None, Hardness 5.75, Elements {'Rh': 78.43, 'S': 21.57}
			Density None, Hardness 5.75, Elements {'Rh': 78.43, 'S': 21.57}
	Found duplicates of "Micheelsenite", with these properties :
			Density 2.15, Hardness 3.75, Elements {'Ca': 12.02, 'Dy': 2.44, 'Y': 13.33, 'Al': 3.64, 'P': 3.25, 'H': 4.64, 'C': 2.16, 'O': 58.53}
			Density 2.15, Hardness 3.75, Elements {'Ca': 12.02, 'Dy': 2.44, 'Y': 13.33, 'Al': 3.64, 'P': 3.25, 'H': 4.64, 'C': 2.16, 'O': 58.53}
	Found duplicates of "Microcline", with these properties :
			Density 2.56, Hardness 6.0, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
			Density 2.56, Hardness 6.0, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
			Density 2.56, Hardness 6.0, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
	Found duplicates of "Microlite", with these properties :
			Density 5.3, Hardness 5.25, Elements {'Na': 6.52, 'Ca': 3.79, 'Ta': 68.41, 'H': 0.06, 'O': 20.87, 'F': 0.36}
			Density 5.3, Hardness 5.25, Elements {'Na': 6.52, 'Ca': 3.79, 'Ta': 68.41, 'H': 0.06, 'O': 20.87, 'F': 0.36}
	Found duplicates of "Middendorfite", with these properties :
			Density 2.6, Hardness 3.25, Elements {'K': 8.44, 'Na': 3.38, 'Ca': 0.09, 'Mg': 0.1, 'Ti': 0.1, 'Mn': 19.31, 'Al': 0.11, 'Zn': 0.14, 'Fe': 0.52, 'Si': 23.82, 'H': 0.87, 'O': 42.89, 'F': 0.23}
			Density 2.6, Hardness 3.25, Elements {'K': 8.44, 'Na': 3.38, 'Ca': 0.09, 'Mg': 0.1, 'Ti': 0.1, 'Mn': 19.31, 'Al': 0.11, 'Zn': 0.14, 'Fe': 0.52, 'Si': 23.82, 'H': 0.87, 'O': 42.89, 'F': 0.23}
	Found duplicates of "Miessiite", with these properties :
			Density None, Hardness 2.25, Elements {'Te': 16.78, 'Pd': 73.78, 'Se': 9.44}
			Density None, Hardness 2.25, Elements {'Te': 16.78, 'Pd': 73.78, 'Se': 9.44}
	Found duplicates of "Millerite", with these properties :
			Density 5.5, Hardness 3.25, Elements {'Ni': 64.67, 'S': 35.33}
			Density 5.5, Hardness 3.25, Elements {'Ni': 64.67, 'S': 35.33}
	Found duplicates of "Millisite", with these properties :
			Density 2.83, Hardness 5.5, Elements {'K': 1.2, 'Na': 2.11, 'Ca': 4.91, 'Al': 19.84, 'P': 15.18, 'H': 1.85, 'O': 54.9}
			Density 2.83, Hardness 5.5, Elements {'K': 1.2, 'Na': 2.11, 'Ca': 4.91, 'Al': 19.84, 'P': 15.18, 'H': 1.85, 'O': 54.9}
	Found duplicates of "Milotaite", with these properties :
			Density 8.09, Hardness 4.5, Elements {'Cu': 0.84, 'Ag': 0.35, 'Sb': 38.03, 'Pd': 34.29, 'Se': 26.48}
			Density 8.09, Hardness 4.5, Elements {'Cu': 0.84, 'Ag': 0.35, 'Sb': 38.03, 'Pd': 34.29, 'Se': 26.48}
	Found duplicates of "Mimetite", with these properties :
			Density None, Hardness None, Elements {'As': 15.1, 'Pb': 69.61, 'Cl': 2.38, 'O': 12.9}
			Density 7.17, Hardness 3.75, Elements {'As': 15.1, 'Pb': 69.61, 'Cl': 2.38, 'O': 12.9}
			Density 7.17, Hardness 3.75, Elements {'As': 15.1, 'Pb': 69.61, 'Cl': 2.38, 'O': 12.9}
			Density 7.17, Hardness 3.75, Elements {'As': 15.1, 'Pb': 69.61, 'Cl': 2.38, 'O': 12.9}
	Found duplicates of "Miserite", with these properties :
			Density 2.88, Hardness 5.75, Elements {'K': 5.57, 'Na': 0.05, 'Ca': 23.71, 'RE': 2.78, 'Y': 1.71, 'Mg': 0.05, 'Ti': 0.05, 'Mn': 0.18, 'Al': 0.03, 'Fe': 0.24, 'Si': 24.05, 'H': 0.26, 'O': 40.2, 'F': 1.12}
			Density 2.88, Hardness 5.75, Elements {'K': 5.57, 'Na': 0.05, 'Ca': 23.71, 'RE': 2.78, 'Y': 1.71, 'Mg': 0.05, 'Ti': 0.05, 'Mn': 0.18, 'Al': 0.03, 'Fe': 0.24, 'Si': 24.05, 'H': 0.26, 'O': 40.2, 'F': 1.12}
	Found duplicates of "Arsenopyrite", with these properties :
			Density 6.07, Hardness 5.0, Elements {'Fe': 34.3, 'As': 46.01, 'S': 19.69}
			Density 6.07, Hardness 5.0, Elements {'Fe': 34.3, 'As': 46.01, 'S': 19.69}
			Density 6.07, Hardness 5.0, Elements {'Fe': 34.3, 'As': 46.01, 'S': 19.69}
			Density 6.07, Hardness 5.0, Elements {'Fe': 34.3, 'As': 46.01, 'S': 19.69}
	Found duplicates of "Copiapite", with these properties :
			Density 2.1, Hardness 2.5, Elements {'Fe': 22.34, 'H': 3.39, 'S': 15.39, 'O': 58.88}
			Density 2.1, Hardness 2.5, Elements {'Fe': 22.34, 'H': 3.39, 'S': 15.39, 'O': 58.88}
			Density 2.1, Hardness 2.5, Elements {'Fe': 22.34, 'H': 3.39, 'S': 15.39, 'O': 58.88}
	Found duplicates of "Orthochrysotile", with these properties :
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
	Found duplicates of "Mitryaevaite", with these properties :
			Density 2.02, Hardness None, Elements {'Al': 15.86, 'P': 12.75, 'H': 3.91, 'S': 1.88, 'O': 61.13, 'F': 4.47}
			Density 2.02, Hardness None, Elements {'Al': 15.86, 'P': 12.75, 'H': 3.91, 'S': 1.88, 'O': 61.13, 'F': 4.47}
	Found duplicates of "Nyboite", with these properties :
			Density None, Hardness None, Elements {'K': 0.14, 'Na': 6.79, 'Ca': 1.7, 'Mg': 8.79, 'Ti': 0.12, 'Al': 7.02, 'Fe': 4.12, 'Si': 24.54, 'Ni': 0.07, 'H': 0.24, 'O': 46.47}
			Density 3.12, Hardness 6.0, Elements {'K': 0.14, 'Na': 6.79, 'Ca': 1.7, 'Mg': 8.79, 'Ti': 0.12, 'Al': 7.02, 'Fe': 4.12, 'Si': 24.54, 'Ni': 0.07, 'H': 0.24, 'O': 46.47}
			Density 3.12, Hardness 6.0, Elements {'K': 0.14, 'Na': 6.79, 'Ca': 1.7, 'Mg': 8.79, 'Ti': 0.12, 'Al': 7.02, 'Fe': 4.12, 'Si': 24.54, 'Ni': 0.07, 'H': 0.24, 'O': 46.47}
	Found duplicates of "Yofortierite", with these properties :
			Density 2.18, Hardness 2.5, Elements {'Ca': 0.8, 'Mg': 1.45, 'Mn': 21.92, 'Al': 0.81, 'Zn': 0.65, 'Si': 22.41, 'H': 2.01, 'O': 49.95}
			Density 2.18, Hardness 2.5, Elements {'Ca': 0.8, 'Mg': 1.45, 'Mn': 21.92, 'Al': 0.81, 'Zn': 0.65, 'Si': 22.41, 'H': 2.01, 'O': 49.95}
			Density 2.18, Hardness 2.5, Elements {'Ca': 0.8, 'Mg': 1.45, 'Mn': 21.92, 'Al': 0.81, 'Zn': 0.65, 'Si': 22.41, 'H': 2.01, 'O': 49.95}
	Found duplicates of "Sphalerite", with these properties :
			Density 4.05, Hardness 3.75, Elements {'Zn': 64.06, 'Fe': 2.88, 'S': 33.06}
			Density 4.05, Hardness 3.75, Elements {'Zn': 64.06, 'Fe': 2.88, 'S': 33.06}
			Density 4.05, Hardness 3.75, Elements {'Zn': 64.06, 'Fe': 2.88, 'S': 33.06}
	Found duplicates of "Moeloite", with these properties :
			Density None, Hardness None, Elements {'Sb': 29.0, 'Pb': 49.36, 'S': 21.64}
			Density None, Hardness None, Elements {'Sb': 29.0, 'Pb': 49.36, 'S': 21.64}
	Found duplicates of "Moganite", with these properties :
			Density None, Hardness None, Elements {'Si': 46.74, 'O': 53.26}
			Density None, Hardness None, Elements {'Si': 46.74, 'O': 53.26}
	Found duplicates of "Mogovidite", with these properties :
			Density 2.9, Hardness 5.5, Elements {'K': 0.31, 'Na': 7.4, 'Ca': 13.14, 'La': 0.14, 'Ce': 0.27, 'Zr': 8.98, 'Ti': 0.14, 'Mn': 0.54, 'Nb': 1.21, 'Fe': 3.75, 'Si': 21.98, 'H': 0.14, 'C': 0.4, 'Cl': 0.53, 'O': 41.06}
			Density 2.9, Hardness 5.5, Elements {'K': 0.31, 'Na': 7.4, 'Ca': 13.14, 'La': 0.14, 'Ce': 0.27, 'Zr': 8.98, 'Ti': 0.14, 'Mn': 0.54, 'Nb': 1.21, 'Fe': 3.75, 'Si': 21.98, 'H': 0.14, 'C': 0.4, 'Cl': 0.53, 'O': 41.06}
	Found duplicates of "Mohrite", with these properties :
			Density 1.83, Hardness 2.25, Elements {'Fe': 14.24, 'H': 5.140000000000001, 'S': 16.35, 'N': 7.14, 'O': 57.12}
			Density 1.83, Hardness 2.25, Elements {'Fe': 14.24, 'H': 5.140000000000001, 'S': 16.35, 'N': 7.14, 'O': 57.12}
	Found duplicates of "Moissanite", with these properties :
			Density 3.21, Hardness 9.5, Elements {'Si': 70.04, 'C': 29.96}
			Density 3.21, Hardness 9.5, Elements {'Si': 70.04, 'C': 29.96}
	Found duplicates of "Molybdenite", with these properties :
			Density 5.5, Hardness 1.0, Elements {'Mo': 59.94, 'S': 40.06}
			Density 5.5, Hardness 1.0, Elements {'Mo': 59.94, 'S': 40.06}
			Density 5.5, Hardness 1.0, Elements {'Mo': 59.94, 'S': 40.06}
			Density 5.5, Hardness 1.0, Elements {'Mo': 59.94, 'S': 40.06}
	Found duplicates of "Bamfordite", with these properties :
			Density 3.62, Hardness 2.5, Elements {'Fe': 13.53, 'Mo': 46.49, 'H': 1.22, 'O': 38.76}
			Density 3.62, Hardness 2.5, Elements {'Fe': 13.53, 'Mo': 46.49, 'H': 1.22, 'O': 38.76}
			Density 3.62, Hardness 2.5, Elements {'Fe': 13.53, 'Mo': 46.49, 'H': 1.22, 'O': 38.76}
	Found duplicates of "Monazite-Sm", with these properties :
			Density None, Hardness None, Elements {'Ca': 1.81, 'Ce': 9.5, 'Sm': 13.59, 'Gd': 14.21, 'Th': 15.73, 'P': 12.6, 'Nd': 6.52, 'O': 26.03}
			Density None, Hardness None, Elements {'Ca': 1.81, 'Ce': 9.5, 'Sm': 13.59, 'Gd': 14.21, 'Th': 15.73, 'P': 12.6, 'Nd': 6.52, 'O': 26.03}
	Found duplicates of "Monipite", with these properties :
			Density None, Hardness None, Elements {'Ni': 31.62, 'Mo': 51.69, 'P': 16.69}
			Density None, Hardness None, Elements {'Ni': 31.62, 'Mo': 51.69, 'P': 16.69}
	Found duplicates of "Monohydrocalcite", with these properties :
			Density 2.38, Hardness 2.5, Elements {'Ca': 33.93, 'H': 1.71, 'C': 10.17, 'O': 54.19}
			Density 2.38, Hardness 2.5, Elements {'Ca': 33.93, 'H': 1.71, 'C': 10.17, 'O': 54.19}
	Found duplicates of "Tetraferriannite", with these properties :
			Density 3.05, Hardness 2.75, Elements {'K': 7.55, 'Mg': 2.35, 'Al': 1.3, 'Fe': 35.06, 'Si': 16.27, 'H': 0.39, 'O': 37.08}
			Density 3.05, Hardness 2.75, Elements {'K': 7.55, 'Mg': 2.35, 'Al': 1.3, 'Fe': 35.06, 'Si': 16.27, 'H': 0.39, 'O': 37.08}
			Density 3.05, Hardness 2.75, Elements {'K': 7.55, 'Mg': 2.35, 'Al': 1.3, 'Fe': 35.06, 'Si': 16.27, 'H': 0.39, 'O': 37.08}
			Density 3.05, Hardness 2.75, Elements {'K': 7.55, 'Mg': 2.35, 'Al': 1.3, 'Fe': 35.06, 'Si': 16.27, 'H': 0.39, 'O': 37.08}
			Density 3.05, Hardness 2.75, Elements {'K': 7.55, 'Mg': 2.35, 'Al': 1.3, 'Fe': 35.06, 'Si': 16.27, 'H': 0.39, 'O': 37.08}
			Density 3.05, Hardness 2.75, Elements {'K': 7.55, 'Mg': 2.35, 'Al': 1.3, 'Fe': 35.06, 'Si': 16.27, 'H': 0.39, 'O': 37.08}
	Found duplicates of "Monsmedite", with these properties :
			Density 3.0, Hardness 2.0, Elements {'K': 5.35, 'Tl': 27.97, 'H': 2.07, 'S': 17.55, 'O': 47.07}
			Density 3.0, Hardness 2.0, Elements {'K': 5.35, 'Tl': 27.97, 'H': 2.07, 'S': 17.55, 'O': 47.07}
	Found duplicates of "Monteregianite-Y", with these properties :
			Density 2.42, Hardness 3.5, Elements {'K': 4.47, 'Na': 6.86, 'Ca': 0.51, 'Y': 9.6, 'Al': 0.34, 'Si': 28.18, 'H': 1.28, 'O': 48.76}
			Density 2.42, Hardness 3.5, Elements {'K': 4.47, 'Na': 6.86, 'Ca': 0.51, 'Y': 9.6, 'Al': 0.34, 'Si': 28.18, 'H': 1.28, 'O': 48.76}
	Found duplicates of "Hydrodelhayelite", with these properties :
			Density 2.168, Hardness 4.0, Elements {'K': 5.17, 'Ca': 10.59, 'Al': 3.56, 'Si': 25.97, 'H': 1.86, 'O': 52.84}
			Density 2.168, Hardness 4.0, Elements {'K': 5.17, 'Ca': 10.59, 'Al': 3.56, 'Si': 25.97, 'H': 1.86, 'O': 52.84}
	Found duplicates of "Montesommaite", with these properties :
			Density 2.34, Hardness None, Elements {'K': 13.82, 'Na': 0.19, 'Al': 10.42, 'Si': 26.09, 'H': 0.83, 'O': 48.66}
			Density 2.34, Hardness None, Elements {'K': 13.82, 'Na': 0.19, 'Al': 10.42, 'Si': 26.09, 'H': 0.83, 'O': 48.66}
	Found duplicates of "Montetrisaite", with these properties :
			Density None, Hardness 2.5, Elements {'Zn': 0.3, 'Cu': 57.59, 'H': 1.84, 'S': 4.53, 'O': 35.75}
			Density None, Hardness 2.5, Elements {'Zn': 0.3, 'Cu': 57.59, 'H': 1.84, 'S': 4.53, 'O': 35.75}
	Found duplicates of "Beidellite", with these properties :
			Density 2.15, Hardness 1.5, Elements {'Na': 2.95, 'Al': 17.33, 'Si': 25.25, 'H': 1.04, 'O': 53.43}
			Density 2.15, Hardness 1.5, Elements {'Na': 2.95, 'Al': 17.33, 'Si': 25.25, 'H': 1.04, 'O': 53.43}
			Density 2.15, Hardness 1.5, Elements {'Na': 2.95, 'Al': 17.33, 'Si': 25.25, 'H': 1.04, 'O': 53.43}
	Found duplicates of "Montmorillonite", with these properties :
			Density 2.35, Hardness 1.75, Elements {'Na': 0.84, 'Ca': 0.73, 'Al': 9.83, 'Si': 20.46, 'H': 4.04, 'O': 64.11}
			Density 2.35, Hardness 1.75, Elements {'Na': 0.84, 'Ca': 0.73, 'Al': 9.83, 'Si': 20.46, 'H': 4.04, 'O': 64.11}
	Found duplicates of "Montroyalite", with these properties :
			Density 2.667, Hardness 3.5, Elements {'Sr': 25.31, 'Al': 15.59, 'H': 2.48, 'C': 2.6, 'O': 37.55, 'F': 16.47}
			Density 2.667, Hardness 3.5, Elements {'Sr': 25.31, 'Al': 15.59, 'H': 2.48, 'C': 2.6, 'O': 37.55, 'F': 16.47}
	Found duplicates of "Moorhouseite", with these properties :
			Density 1.97, Hardness 2.5, Elements {'Mn': 2.09, 'Co': 13.46, 'Ni': 6.7, 'H': 4.61, 'S': 12.21, 'O': 60.92}
			Density 1.97, Hardness 2.5, Elements {'Mn': 2.09, 'Co': 13.46, 'Ni': 6.7, 'H': 4.61, 'S': 12.21, 'O': 60.92}
	Found duplicates of "Mordenite", with these properties :
			Density 2.12, Hardness 5.0, Elements {'K': 0.45, 'Na': 2.89, 'Ca': 2.29, 'Al': 6.79, 'Si': 31.49, 'H': 1.36, 'O': 54.73}
			Density 2.12, Hardness 5.0, Elements {'K': 0.45, 'Na': 2.89, 'Ca': 2.29, 'Al': 6.79, 'Si': 31.49, 'H': 1.36, 'O': 54.73}
			Density 2.12, Hardness 5.0, Elements {'K': 0.45, 'Na': 2.89, 'Ca': 2.29, 'Al': 6.79, 'Si': 31.49, 'H': 1.36, 'O': 54.73}
			Density 2.12, Hardness 5.0, Elements {'K': 0.45, 'Na': 2.89, 'Ca': 2.29, 'Al': 6.79, 'Si': 31.49, 'H': 1.36, 'O': 54.73}
			Density 2.12, Hardness 5.0, Elements {'K': 0.45, 'Na': 2.89, 'Ca': 2.29, 'Al': 6.79, 'Si': 31.49, 'H': 1.36, 'O': 54.73}
	Found duplicates of "Beryl", with these properties :
			Density 2.76, Hardness 7.75, Elements {'Be': 5.03, 'Al': 10.04, 'Si': 31.35, 'O': 53.58}
			Density 2.76, Hardness 7.75, Elements {'Be': 5.03, 'Al': 10.04, 'Si': 31.35, 'O': 53.58}
			Density 2.76, Hardness 7.75, Elements {'Be': 5.03, 'Al': 10.04, 'Si': 31.35, 'O': 53.58}
			Density 2.76, Hardness 7.75, Elements {'Be': 5.03, 'Al': 10.04, 'Si': 31.35, 'O': 53.58}
			Density 2.76, Hardness 7.75, Elements {'Be': 5.03, 'Al': 10.04, 'Si': 31.35, 'O': 53.58}
			Density 2.76, Hardness 7.75, Elements {'Be': 5.03, 'Al': 10.04, 'Si': 31.35, 'O': 53.58}
	Found duplicates of "Morimotoite", with these properties :
			Density 3.75, Hardness 7.5, Elements {'Ca': 24.04, 'Ti': 9.57, 'Fe': 11.16, 'Si': 16.84, 'O': 38.38}
			Density 3.75, Hardness 7.5, Elements {'Ca': 24.04, 'Ti': 9.57, 'Fe': 11.16, 'Si': 16.84, 'O': 38.38}
	Found duplicates of "Morinite", with these properties :
			Density 2.962, Hardness 4.25, Elements {'Na': 4.83, 'Ca': 16.85, 'Al': 11.35, 'P': 13.03, 'H': 1.11, 'O': 37.85, 'F': 14.98}
			Density 2.962, Hardness 4.25, Elements {'Na': 4.83, 'Ca': 16.85, 'Al': 11.35, 'P': 13.03, 'H': 1.11, 'O': 37.85, 'F': 14.98}
	Found duplicates of "Mosandrite", with these properties :
			Density 3.29, Hardness 4.0, Elements {'Na': 5.03, 'Ca': 13.16, 'Ce': 23.01, 'Y': 4.87, 'Zr': 1.0, 'Ti': 3.15, 'Nb': 3.05, 'Si': 12.3, 'O': 27.15, 'F': 7.28}
			Density 3.29, Hardness 4.0, Elements {'Na': 5.03, 'Ca': 13.16, 'Ce': 23.01, 'Y': 4.87, 'Zr': 1.0, 'Ti': 3.15, 'Nb': 3.05, 'Si': 12.3, 'O': 27.15, 'F': 7.28}
	Found duplicates of "Moskvinite-Y", with these properties :
			Density 2.91, Hardness 5.0, Elements {'K': 6.21, 'Na': 7.91, 'Sm': 0.5, 'Gd': 1.05, 'Dy': 2.44, 'Y': 11.44, 'Ho': 0.55, 'Er': 1.12, 'Tb': 0.27, 'Si': 28.16, 'Nd': 0.24, 'O': 40.11}
			Density 2.91, Hardness 5.0, Elements {'K': 6.21, 'Na': 7.91, 'Sm': 0.5, 'Gd': 1.05, 'Dy': 2.44, 'Y': 11.44, 'Ho': 0.55, 'Er': 1.12, 'Tb': 0.27, 'Si': 28.16, 'Nd': 0.24, 'O': 40.11}
	Found duplicates of "Quartz", with these properties :
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.62, Hardness 7.0, Elements {'Si': 46.74, 'O': 53.26}
	Found duplicates of "Mottanaite-Ce", with these properties :
			Density 3.61, Hardness None, Elements {'Ca': 18.31, 'Ce': 20.87, 'Th': 2.3, 'Ti': 0.48, 'Be': 0.72, 'Al': 1.34, 'Fe': 2.22, 'Si': 11.15, 'B': 4.29, 'H': 0.05, 'O': 37.33, 'F': 0.94}
			Density 3.61, Hardness None, Elements {'Ca': 18.31, 'Ce': 20.87, 'Th': 2.3, 'Ti': 0.48, 'Be': 0.72, 'Al': 1.34, 'Fe': 2.22, 'Si': 11.15, 'B': 4.29, 'H': 0.05, 'O': 37.33, 'F': 0.94}
	Found duplicates of "Mottramite", with these properties :
			Density 5.95, Hardness 3.5, Elements {'V': 12.65, 'Cu': 15.78, 'H': 0.25, 'Pb': 51.45, 'O': 19.87}
			Density 5.95, Hardness 3.5, Elements {'V': 12.65, 'Cu': 15.78, 'H': 0.25, 'Pb': 51.45, 'O': 19.87}
			Density 5.95, Hardness 3.5, Elements {'V': 12.65, 'Cu': 15.78, 'H': 0.25, 'Pb': 51.45, 'O': 19.87}
	Found duplicates of "Clinochrysotile", with these properties :
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
	Found duplicates of "Moydite-Y", with these properties :
			Density 3.13, Hardness 1.5, Elements {'Y': 39.04, 'B': 4.75, 'H': 1.77, 'C': 5.27, 'O': 49.17}
			Density 3.13, Hardness 1.5, Elements {'Y': 39.04, 'B': 4.75, 'H': 1.77, 'C': 5.27, 'O': 49.17}
	Found duplicates of "Mozartite", with these properties :
			Density 3.64, Hardness 6.0, Elements {'Ca': 19.64, 'Mn': 26.92, 'Si': 13.76, 'H': 0.49, 'O': 39.19}
			Density 3.64, Hardness 6.0, Elements {'Ca': 19.64, 'Mn': 26.92, 'Si': 13.76, 'H': 0.49, 'O': 39.19}
	Found duplicates of "Mozgovaite", with these properties :
			Density None, Hardness 3.0, Elements {'Bi': 65.34, 'Pb': 16.2, 'Se': 1.54, 'S': 16.92}
			Density None, Hardness 3.0, Elements {'Bi': 65.34, 'Pb': 16.2, 'Se': 1.54, 'S': 16.92}
	Found duplicates of "Mrazekite", with these properties :
			Density 4.9, Hardness 2.5, Elements {'Cu': 21.17, 'Bi': 46.41, 'P': 6.88, 'H': 0.67, 'O': 24.87}
			Density 4.9, Hardness 2.5, Elements {'Cu': 21.17, 'Bi': 46.41, 'P': 6.88, 'H': 0.67, 'O': 24.87}
	Found duplicates of "Muckeite", with these properties :
			Density 5.88, Hardness 3.5, Elements {'Cu': 14.87, 'Ni': 13.73, 'Bi': 48.89, 'S': 22.51}
			Density 5.88, Hardness 3.5, Elements {'Cu': 14.87, 'Ni': 13.73, 'Bi': 48.89, 'S': 22.51}
	Found duplicates of "Boulangerite", with these properties :
			Density 6.0, Hardness 2.5, Elements {'Sb': 26.44, 'Pb': 54.88, 'S': 18.68}
			Density 6.0, Hardness 2.5, Elements {'Sb': 26.44, 'Pb': 54.88, 'S': 18.68}
	Found duplicates of "Mummeite", with these properties :
			Density 6.64, Hardness 4.0, Elements {'Cu': 1.53, 'Ag': 13.94, 'Bi': 57.74, 'Pb': 9.47, 'S': 17.32}
			Density 6.64, Hardness 4.0, Elements {'Cu': 1.53, 'Ag': 13.94, 'Bi': 57.74, 'Pb': 9.47, 'S': 17.32}
	Found duplicates of "Munakataite", with these properties :
			Density None, Hardness 1.5, Elements {'Ca': 0.05, 'Cu': 14.75, 'H': 0.47, 'Pb': 50.32, 'Se': 9.45, 'S': 3.91, 'O': 21.05}
			Density None, Hardness 1.5, Elements {'Ca': 0.05, 'Cu': 14.75, 'H': 0.47, 'Pb': 50.32, 'Se': 9.45, 'S': 3.91, 'O': 21.05}
	Found duplicates of "Halite", with these properties :
			Density 2.17, Hardness 2.5, Elements {'Na': 39.34, 'Cl': 60.66}
			Density 2.17, Hardness 2.5, Elements {'Na': 39.34, 'Cl': 60.66}
			Density 2.17, Hardness 2.5, Elements {'Na': 39.34, 'Cl': 60.66}
			Density 2.17, Hardness 2.5, Elements {'Na': 39.34, 'Cl': 60.66}
	Found duplicates of "Muscovite", with these properties :
			Density 2.82, Hardness 2.25, Elements {'K': 9.81, 'Al': 20.3, 'Si': 21.13, 'H': 0.46, 'O': 47.35, 'F': 0.95}
			Density 2.82, Hardness 2.25, Elements {'K': 9.81, 'Al': 20.3, 'Si': 21.13, 'H': 0.46, 'O': 47.35, 'F': 0.95}
			Density 2.82, Hardness 2.25, Elements {'K': 9.81, 'Al': 20.3, 'Si': 21.13, 'H': 0.46, 'O': 47.35, 'F': 0.95}
			Density 2.82, Hardness 2.25, Elements {'K': 9.81, 'Al': 20.3, 'Si': 21.13, 'H': 0.46, 'O': 47.35, 'F': 0.95}
			Density 2.82, Hardness 2.25, Elements {'K': 9.81, 'Al': 20.3, 'Si': 21.13, 'H': 0.46, 'O': 47.35, 'F': 0.95}
	Found duplicates of "Museumite", with these properties :
			Density None, Hardness 2.0, Elements {'Sb': 6.17, 'Te': 11.72, 'Pb': 51.99, 'Au': 10.68, 'S': 19.44}
			Density None, Hardness 2.0, Elements {'Sb': 6.17, 'Te': 11.72, 'Pb': 51.99, 'Au': 10.68, 'S': 19.44}
	Found duplicates of "Magnesiotaaffeite-6N3S", with these properties :
			Density 3.68, Hardness 8.25, Elements {'Mg': 9.69, 'Be': 1.9, 'Al': 37.95, 'Zn': 1.53, 'Fe': 3.93, 'O': 45.0}
			Density 3.68, Hardness 8.25, Elements {'Mg': 9.69, 'Be': 1.9, 'Al': 37.95, 'Zn': 1.53, 'Fe': 3.93, 'O': 45.0}
	Found duplicates of "Muskoxite", with these properties :
			Density 3.17, Hardness 3.0, Elements {'Mg': 21.77, 'Fe': 28.58, 'H': 2.58, 'O': 47.08}
			Density 3.17, Hardness 3.0, Elements {'Mg': 21.77, 'Fe': 28.58, 'H': 2.58, 'O': 47.08}
	Found duplicates of "Mutinaite", with these properties :
			Density 2.14, Hardness None, Elements {'Na': 0.98, 'Ca': 2.27, 'Al': 4.2, 'Si': 33.78, 'H': 1.71, 'O': 57.06}
			Density 2.14, Hardness None, Elements {'Na': 0.98, 'Ca': 2.27, 'Al': 4.2, 'Si': 33.78, 'H': 1.71, 'O': 57.06}
	Found duplicates of "Mutnovskite", with these properties :
			Density None, Hardness 2.0, Elements {'Bi': 0.62, 'As': 10.96, 'Pb': 61.52, 'Se': 0.24, 'S': 14.26, 'I': 8.9, 'Br': 1.07, 'Cl': 2.43}
			Density None, Hardness 2.0, Elements {'Bi': 0.62, 'As': 10.96, 'Pb': 61.52, 'Se': 0.24, 'S': 14.26, 'I': 8.9, 'Br': 1.07, 'Cl': 2.43}
	Found duplicates of "Rectorite", with these properties :
			Density None, Hardness 1.5, Elements {'K': 0.5, 'Na': 1.76, 'Ca': 1.53, 'Al': 20.64, 'Si': 21.49, 'H': 1.03, 'O': 53.05}
			Density None, Hardness 1.5, Elements {'K': 0.5, 'Na': 1.76, 'Ca': 1.53, 'Al': 20.64, 'Si': 21.49, 'H': 1.03, 'O': 53.05}
			Density None, Hardness 1.5, Elements {'K': 0.5, 'Na': 1.76, 'Ca': 1.53, 'Al': 20.64, 'Si': 21.49, 'H': 1.03, 'O': 53.05}
			Density None, Hardness 1.5, Elements {'K': 0.5, 'Na': 1.76, 'Ca': 1.53, 'Al': 20.64, 'Si': 21.49, 'H': 1.03, 'O': 53.05}
			Density None, Hardness 1.5, Elements {'K': 0.5, 'Na': 1.76, 'Ca': 1.53, 'Al': 20.64, 'Si': 21.49, 'H': 1.03, 'O': 53.05}
			Density None, Hardness 1.5, Elements {'K': 0.5, 'Na': 1.76, 'Ca': 1.53, 'Al': 20.64, 'Si': 21.49, 'H': 1.03, 'O': 53.05}
	Found duplicates of "Natrokomarovite", with these properties :
			Density 3.3, Hardness 4.0, Elements {'Na': 9.13, 'Ca': 2.36, 'Ce': 1.03, 'Ti': 2.11, 'Nb': 38.26, 'Si': 8.26, 'H': 0.59, 'O': 35.18, 'F': 3.07}
			Density 3.3, Hardness 4.0, Elements {'Na': 9.13, 'Ca': 2.36, 'Ce': 1.03, 'Ti': 2.11, 'Nb': 38.26, 'Si': 8.26, 'H': 0.59, 'O': 35.18, 'F': 3.07}
			Density 3.3, Hardness 4.0, Elements {'Na': 9.13, 'Ca': 2.36, 'Ce': 1.03, 'Ti': 2.11, 'Nb': 38.26, 'Si': 8.26, 'H': 0.59, 'O': 35.18, 'F': 3.07}
			Density 3.3, Hardness 4.0, Elements {'Na': 9.13, 'Ca': 2.36, 'Ce': 1.03, 'Ti': 2.11, 'Nb': 38.26, 'Si': 8.26, 'H': 0.59, 'O': 35.18, 'F': 3.07}
	Found duplicates of "Nabalamprophyllite", with these properties :
			Density 3.58, Hardness 3.0, Elements {'K': 0.77, 'Ba': 21.86, 'Na': 8.37, 'Sr': 0.54, 'Ca': 0.25, 'Mg': 0.21, 'Ti': 16.84, 'Mn': 0.88, 'Al': 0.23, 'Fe': 0.55, 'Si': 13.58, 'H': 0.21, 'O': 34.52, 'F': 1.2}
			Density 3.58, Hardness 3.0, Elements {'K': 0.77, 'Ba': 21.86, 'Na': 8.37, 'Sr': 0.54, 'Ca': 0.25, 'Mg': 0.21, 'Ti': 16.84, 'Mn': 0.88, 'Al': 0.23, 'Fe': 0.55, 'Si': 13.58, 'H': 0.21, 'O': 34.52, 'F': 1.2}
	Found duplicates of "Nabesite", with these properties :
			Density 2.16, Hardness 5.5, Elements {'Na': 10.27, 'Be': 2.3, 'Si': 28.67, 'H': 2.01, 'O': 56.76}
			Density 2.16, Hardness 5.5, Elements {'Na': 10.27, 'Be': 2.3, 'Si': 28.67, 'H': 2.01, 'O': 56.76}
	Found duplicates of "Nabiasite", with these properties :
			Density None, Hardness 4.25, Elements {'Ba': 10.01, 'Mn': 36.03, 'V': 19.68, 'As': 3.82, 'H': 0.15, 'O': 30.32}
			Density None, Hardness 4.25, Elements {'Ba': 10.01, 'Mn': 36.03, 'V': 19.68, 'As': 3.82, 'H': 0.15, 'O': 30.32}
	Found duplicates of "Nafertisite", with these properties :
			Density 2.7, Hardness 2.5, Elements {'Na': 4.51, 'Ti': 6.26, 'Fe': 21.89, 'Si': 22.02, 'H': 0.38, 'O': 44.95}
			Density 2.7, Hardness 2.5, Elements {'Na': 4.51, 'Ti': 6.26, 'Fe': 21.89, 'Si': 22.02, 'H': 0.38, 'O': 44.95}
	Found duplicates of "Nahcolite", with these properties :
			Density 2.21, Hardness 2.5, Elements {'Na': 27.37, 'H': 1.2, 'C': 14.3, 'O': 57.14}
			Density 2.21, Hardness 2.5, Elements {'Na': 27.37, 'H': 1.2, 'C': 14.3, 'O': 57.14}
			Density 2.21, Hardness 2.5, Elements {'Na': 27.37, 'H': 1.2, 'C': 14.3, 'O': 57.14}
	Found duplicates of "Nahpoite", with these properties :
			Density 2.58, Hardness 1.5, Elements {'Na': 32.39, 'P': 21.82, 'H': 0.71, 'O': 45.08}
			Density 2.58, Hardness 1.5, Elements {'Na': 32.39, 'P': 21.82, 'H': 0.71, 'O': 45.08}
	Found duplicates of "Gagarinite-Y", with these properties :
			Density 4.21, Hardness 4.5, Elements {'Na': 7.91, 'Ca': 13.79, 'Y': 30.59, 'Cl': 18.3, 'F': 29.41}
			Density 4.21, Hardness 4.5, Elements {'Na': 7.91, 'Ca': 13.79, 'Y': 30.59, 'Cl': 18.3, 'F': 29.41}
	Found duplicates of "Naldretteite", with these properties :
			Density None, Hardness 4.5, Elements {'Fe': 0.12, 'Sb': 35.86, 'As': 0.31, 'Pd': 63.69, 'S': 0.02}
			Density None, Hardness 4.5, Elements {'Fe': 0.12, 'Sb': 35.86, 'As': 0.31, 'Pd': 63.69, 'S': 0.02}
	Found duplicates of "Nalipoite", with these properties :
			Density 2.58, Hardness 4.0, Elements {'Na': 17.44, 'Li': 10.53, 'P': 23.49, 'O': 48.54}
			Density 2.58, Hardness 4.0, Elements {'Na': 17.44, 'Li': 10.53, 'P': 23.49, 'O': 48.54}
	Found duplicates of "Nalivkinite", with these properties :
			Density None, Hardness None, Elements {'Cs': 0.91, 'K': 2.24, 'Na': 1.4, 'Li': 0.6, 'Ca': 0.89, 'Mg': 0.07, 'Zr': 1.11, 'Ta': 0.55, 'Ti': 5.7, 'Mn': 3.78, 'Nb': 1.7, 'Al': 0.31, 'Zn': 0.2, 'Fe': 24.05, 'Si': 16.92, 'Sn': 0.82, 'H': 0.31, 'Pb': 0.32, 'O': 36.65, 'F': 1.45}
			Density None, Hardness None, Elements {'Cs': 0.91, 'K': 2.24, 'Na': 1.4, 'Li': 0.6, 'Ca': 0.89, 'Mg': 0.07, 'Zr': 1.11, 'Ta': 0.55, 'Ti': 5.7, 'Mn': 3.78, 'Nb': 1.7, 'Al': 0.31, 'Zn': 0.2, 'Fe': 24.05, 'Si': 16.92, 'Sn': 0.82, 'H': 0.31, 'Pb': 0.32, 'O': 36.65, 'F': 1.45}
	Found duplicates of "Namansilite", with these properties :
			Density 3.6, Hardness 6.5, Elements {'Na': 9.99, 'Mn': 23.88, 'Si': 24.41, 'O': 41.72}
			Density 3.6, Hardness 6.5, Elements {'Na': 9.99, 'Mn': 23.88, 'Si': 24.41, 'O': 41.72}
	Found duplicates of "Alum-K", with these properties :
			Density 1.76, Hardness 2.0, Elements {'K': 8.24, 'Al': 5.69, 'H': 5.1, 'S': 13.52, 'O': 67.45}
			Density 1.76, Hardness 2.0, Elements {'K': 8.24, 'Al': 5.69, 'H': 5.1, 'S': 13.52, 'O': 67.45}
			Density 1.76, Hardness 2.0, Elements {'K': 8.24, 'Al': 5.69, 'H': 5.1, 'S': 13.52, 'O': 67.45}
			Density 1.76, Hardness 2.0, Elements {'K': 8.24, 'Al': 5.69, 'H': 5.1, 'S': 13.52, 'O': 67.45}
	Found duplicates of "Aspidolite", with these properties :
			Density None, Hardness 2.5, Elements {'K': 0.95, 'Na': 5.04, 'Mg': 13.43, 'Ti': 0.58, 'Al': 12.15, 'Fe': 3.13, 'Si': 17.5, 'H': 0.48, 'O': 46.61, 'F': 0.14}
			Density None, Hardness 2.5, Elements {'K': 0.95, 'Na': 5.04, 'Mg': 13.43, 'Ti': 0.58, 'Al': 12.15, 'Fe': 3.13, 'Si': 17.5, 'H': 0.48, 'O': 46.61, 'F': 0.14}
			Density None, Hardness 2.5, Elements {'K': 0.95, 'Na': 5.04, 'Mg': 13.43, 'Ti': 0.58, 'Al': 12.15, 'Fe': 3.13, 'Si': 17.5, 'H': 0.48, 'O': 46.61, 'F': 0.14}
			Density None, Hardness 2.5, Elements {'K': 0.95, 'Na': 5.04, 'Mg': 13.43, 'Ti': 0.58, 'Al': 12.15, 'Fe': 3.13, 'Si': 17.5, 'H': 0.48, 'O': 46.61, 'F': 0.14}
			Density None, Hardness 2.5, Elements {'K': 0.95, 'Na': 5.04, 'Mg': 13.43, 'Ti': 0.58, 'Al': 12.15, 'Fe': 3.13, 'Si': 17.5, 'H': 0.48, 'O': 46.61, 'F': 0.14}
	Found duplicates of "Natroalunite", with these properties :
			Density 2.75, Hardness 3.75, Elements {'Na': 5.77, 'Al': 20.33, 'H': 1.52, 'S': 16.11, 'O': 56.26}
			Density 2.75, Hardness 3.75, Elements {'Na': 5.77, 'Al': 20.33, 'H': 1.52, 'S': 16.11, 'O': 56.26}
	Found duplicates of "Apophyllite-NaF", with these properties :
			Density 2.34, Hardness 4.5, Elements {'Na': 2.58, 'Ca': 17.99, 'Si': 25.21, 'H': 1.81, 'O': 50.27, 'F': 2.13}
			Density 2.34, Hardness 4.5, Elements {'Na': 2.58, 'Ca': 17.99, 'Si': 25.21, 'H': 1.81, 'O': 50.27, 'F': 2.13}
	Found duplicates of "Natrobetpakdalite", with these properties :
			Density 2.92, Hardness None, Elements {'Na': 2.8, 'Ca': 2.44, 'Fe': 6.8, 'Mo': 35.07, 'As': 9.13, 'H': 1.84, 'O': 41.91}
			Density 2.92, Hardness None, Elements {'Na': 2.8, 'Ca': 2.44, 'Fe': 6.8, 'Mo': 35.07, 'As': 9.13, 'H': 1.84, 'O': 41.91}
			Density 2.92, Hardness None, Elements {'Na': 2.8, 'Ca': 2.44, 'Fe': 6.8, 'Mo': 35.07, 'As': 9.13, 'H': 1.84, 'O': 41.91}
	Found duplicates of "Natroboltwoodite", with these properties :
			Density 4.1, Hardness 3.75, Elements {'K': 1.4, 'Na': 2.48, 'U': 68.38, 'Si': 4.03, 'H': 0.72, 'O': 22.98}
			Density 4.1, Hardness 3.75, Elements {'K': 1.4, 'Na': 2.48, 'U': 68.38, 'Si': 4.03, 'H': 0.72, 'O': 22.98}
			Density 4.1, Hardness 3.75, Elements {'K': 1.4, 'Na': 2.48, 'U': 68.38, 'Si': 4.03, 'H': 0.72, 'O': 22.98}
	Found duplicates of "Gaylussite", with these properties :
			Density 1.96, Hardness 2.5, Elements {'Na': 15.53, 'Ca': 13.53, 'H': 3.4, 'C': 8.11, 'O': 59.43}
			Density 1.96, Hardness 2.5, Elements {'Na': 15.53, 'Ca': 13.53, 'H': 3.4, 'C': 8.11, 'O': 59.43}
	Found duplicates of "Natroglaucocerinite", with these properties :
			Density None, Hardness None, Elements {'Na': 9.61, 'Al': 9.67, 'Zn': 7.81, 'Cu': 3.8, 'H': 4.09, 'S': 5.75, 'O': 59.26}
			Density None, Hardness None, Elements {'Na': 9.61, 'Al': 9.67, 'Zn': 7.81, 'Cu': 3.8, 'H': 4.09, 'S': 5.75, 'O': 59.26}
	Found duplicates of "Natrolemoynite", with these properties :
			Density 2.47, Hardness 3.0, Elements {'K': 1.04, 'Na': 5.5, 'Ca': 0.36, 'Zr': 16.17, 'Nb': 0.82, 'Si': 24.89, 'H': 1.61, 'O': 49.62}
			Density 2.47, Hardness 3.0, Elements {'K': 1.04, 'Na': 5.5, 'Ca': 0.36, 'Zr': 16.17, 'Nb': 0.82, 'Si': 24.89, 'H': 1.61, 'O': 49.62}
	Found duplicates of "Natromontebrasite", with these properties :
			Density 3.04, Hardness 5.5, Elements {'Na': 10.88, 'Li': 1.1, 'Al': 17.03, 'P': 19.55, 'H': 0.48, 'O': 47.97, 'F': 3.0}
			Density 3.04, Hardness 5.5, Elements {'Na': 10.88, 'Li': 1.1, 'Al': 17.03, 'P': 19.55, 'H': 0.48, 'O': 47.97, 'F': 3.0}
	Found duplicates of "Natron", with these properties :
			Density 1.44, Hardness 1.0, Elements {'Na': 16.07, 'H': 7.05, 'C': 4.2, 'O': 72.69}
			Density 1.44, Hardness 1.0, Elements {'Na': 16.07, 'H': 7.05, 'C': 4.2, 'O': 72.69}
	Found duplicates of "Natropharmacosiderite", with these properties :
			Density 2.79, Hardness 3.0, Elements {'K': 2.17, 'Na': 4.33, 'Fe': 24.75, 'As': 24.07, 'H': 2.14, 'O': 42.54}
			Density 2.79, Hardness 3.0, Elements {'K': 2.17, 'Na': 4.33, 'Fe': 24.75, 'As': 24.07, 'H': 2.14, 'O': 42.54}
			Density 2.79, Hardness 3.0, Elements {'K': 2.17, 'Na': 4.33, 'Fe': 24.75, 'As': 24.07, 'H': 2.14, 'O': 42.54}
	Found duplicates of "Natroxalate", with these properties :
			Density 2.32, Hardness 3.0, Elements {'Na': 34.31, 'C': 17.93, 'O': 47.76}
			Density 2.32, Hardness 3.0, Elements {'Na': 34.31, 'C': 17.93, 'O': 47.76}
	Found duplicates of "Natrozippeite", with these properties :
			Density 4.3, Hardness 5.25, Elements {'Na': 3.74, 'U': 58.09, 'H': 1.72, 'S': 3.91, 'O': 32.54}
			Density 4.3, Hardness 5.25, Elements {'Na': 3.74, 'U': 58.09, 'H': 1.72, 'S': 3.91, 'O': 32.54}
			Density 4.3, Hardness 5.25, Elements {'Na': 3.74, 'U': 58.09, 'H': 1.72, 'S': 3.91, 'O': 32.54}
	Found duplicates of "Nchwaningite", with these properties :
			Density 3.19, Hardness 5.5, Elements {'Mn': 46.17, 'Si': 11.8, 'H': 1.69, 'O': 40.34}
			Density 3.19, Hardness 5.5, Elements {'Mn': 46.17, 'Si': 11.8, 'H': 1.69, 'O': 40.34}
	Found duplicates of "Nechelyustovite", with these properties :
			Density 3.37, Hardness None, Elements {'K': 0.76, 'Ba': 11.7, 'Na': 5.75, 'Sr': 2.49, 'Ca': 0.96, 'Ce': 0.32, 'Ti': 12.35, 'Mn': 3.87, 'Nb': 7.07, 'Fe': 0.25, 'Si': 12.76, 'H': 1.15, 'O': 40.58}
			Density 3.37, Hardness None, Elements {'K': 0.76, 'Ba': 11.7, 'Na': 5.75, 'Sr': 2.49, 'Ca': 0.96, 'Ce': 0.32, 'Ti': 12.35, 'Mn': 3.87, 'Nb': 7.07, 'Fe': 0.25, 'Si': 12.76, 'H': 1.15, 'O': 40.58}
	Found duplicates of "Goethite", with these properties :
			Density 3.8, Hardness 5.25, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
			Density 3.8, Hardness 5.25, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
			Density 3.8, Hardness 5.25, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
			Density 3.8, Hardness 5.25, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
	Found duplicates of "Nelenite", with these properties :
			Density 3.46, Hardness 5.0, Elements {'Mg': 0.11, 'Mn': 22.57, 'Zn': 2.83, 'Fe': 13.28, 'Si': 14.57, 'As': 9.4, 'H': 0.72, 'O': 36.53}
			Density 3.46, Hardness 5.0, Elements {'Mg': 0.11, 'Mn': 22.57, 'Zn': 2.83, 'Fe': 13.28, 'Si': 14.57, 'As': 9.4, 'H': 0.72, 'O': 36.53}
	Found duplicates of "Brucite", with these properties :
			Density 2.39, Hardness 2.75, Elements {'Mg': 41.68, 'H': 3.46, 'O': 54.87}
			Density 2.39, Hardness 2.75, Elements {'Mg': 41.68, 'H': 3.46, 'O': 54.87}
	Found duplicates of "Boltwoodite", with these properties :
			Density 3.6, Hardness 3.75, Elements {'K': 9.11, 'U': 55.45, 'Si': 6.54, 'H': 0.94, 'O': 27.96}
			Density 3.6, Hardness 3.75, Elements {'K': 9.11, 'U': 55.45, 'Si': 6.54, 'H': 0.94, 'O': 27.96}
	Found duplicates of "Colemanite", with these properties :
			Density 2.42, Hardness 4.5, Elements {'Ca': 19.5, 'B': 15.78, 'H': 2.45, 'O': 62.27}
			Density 2.42, Hardness 4.5, Elements {'Ca': 19.5, 'B': 15.78, 'H': 2.45, 'O': 62.27}
	Found duplicates of "Lanthanite-Nd", with these properties :
			Density 2.81, Hardness 2.75, Elements {'La': 11.39, 'H': 2.64, 'C': 5.91, 'Nd': 35.47, 'O': 44.59}
			Density 2.81, Hardness 2.75, Elements {'La': 11.39, 'H': 2.64, 'C': 5.91, 'Nd': 35.47, 'O': 44.59}
			Density 2.81, Hardness 2.75, Elements {'La': 11.39, 'H': 2.64, 'C': 5.91, 'Nd': 35.47, 'O': 44.59}
			Density 2.81, Hardness 2.75, Elements {'La': 11.39, 'H': 2.64, 'C': 5.91, 'Nd': 35.47, 'O': 44.59}
	Found duplicates of "Neotocite", with these properties :
			Density 2.8, Hardness 3.5, Elements {'Mn': 27.6, 'Fe': 9.35, 'Si': 18.82, 'H': 1.35, 'O': 42.88}
			Density 2.8, Hardness 3.5, Elements {'Mn': 27.6, 'Fe': 9.35, 'Si': 18.82, 'H': 1.35, 'O': 42.88}
			Density 2.8, Hardness 3.5, Elements {'Mn': 27.6, 'Fe': 9.35, 'Si': 18.82, 'H': 1.35, 'O': 42.88}
			Density 2.8, Hardness 3.5, Elements {'Mn': 27.6, 'Fe': 9.35, 'Si': 18.82, 'H': 1.35, 'O': 42.88}
	Found duplicates of "Nepheline", with these properties :
			Density 2.59, Hardness 6.0, Elements {'K': 6.69, 'Na': 11.8, 'Al': 18.47, 'Si': 19.23, 'O': 43.81}
			Density 2.59, Hardness 6.0, Elements {'K': 6.69, 'Na': 11.8, 'Al': 18.47, 'Si': 19.23, 'O': 43.81}
			Density 2.59, Hardness 6.0, Elements {'K': 6.69, 'Na': 11.8, 'Al': 18.47, 'Si': 19.23, 'O': 43.81}
	Found duplicates of "Actinolite", with these properties :
			Density 3.04, Hardness 5.5, Elements {'Na': 0.59, 'Ca': 8.6, 'Mg': 9.71, 'Ti': 0.11, 'Mn': 0.13, 'Al': 1.39, 'Fe': 8.58, 'Si': 25.64, 'H': 0.24, 'O': 45.01}
			Density 3.04, Hardness 5.5, Elements {'Na': 0.59, 'Ca': 8.6, 'Mg': 9.71, 'Ti': 0.11, 'Mn': 0.13, 'Al': 1.39, 'Fe': 8.58, 'Si': 25.64, 'H': 0.24, 'O': 45.01}
			Density 3.04, Hardness 5.5, Elements {'Na': 0.59, 'Ca': 8.6, 'Mg': 9.71, 'Ti': 0.11, 'Mn': 0.13, 'Al': 1.39, 'Fe': 8.58, 'Si': 25.64, 'H': 0.24, 'O': 45.01}
			Density 3.04, Hardness 5.5, Elements {'Na': 0.59, 'Ca': 8.6, 'Mg': 9.71, 'Ti': 0.11, 'Mn': 0.13, 'Al': 1.39, 'Fe': 8.58, 'Si': 25.64, 'H': 0.24, 'O': 45.01}
			Density 3.04, Hardness 5.5, Elements {'Na': 0.59, 'Ca': 8.6, 'Mg': 9.71, 'Ti': 0.11, 'Mn': 0.13, 'Al': 1.39, 'Fe': 8.58, 'Si': 25.64, 'H': 0.24, 'O': 45.01}
	Found duplicates of "Nepouite", with these properties :
			Density 2.85, Hardness 2.25, Elements {'Si': 14.77, 'Ni': 46.3, 'H': 1.06, 'O': 37.87}
			Density 2.85, Hardness 2.25, Elements {'Si': 14.77, 'Ni': 46.3, 'H': 1.06, 'O': 37.87}
	Found duplicates of "Nepskoeite", with these properties :
			Density 1.76, Hardness 1.75, Elements {'Mg': 27.02, 'H': 5.32, 'Cl': 9.85, 'O': 57.81}
			Density 1.76, Hardness 1.75, Elements {'Mg': 27.02, 'H': 5.32, 'Cl': 9.85, 'O': 57.81}
	Found duplicates of "Neptunite", with these properties :
			Density 3.23, Hardness 5.5, Elements {'K': 4.31, 'Na': 5.07, 'Li': 0.76, 'Ti': 10.55, 'Mn': 3.03, 'Fe': 9.23, 'Si': 24.75, 'O': 42.3}
			Density 3.23, Hardness 5.5, Elements {'K': 4.31, 'Na': 5.07, 'Li': 0.76, 'Ti': 10.55, 'Mn': 3.03, 'Fe': 9.23, 'Si': 24.75, 'O': 42.3}
	Found duplicates of "Neskevaaraite-Fe", with these properties :
			Density 2.88, Hardness 5.0, Elements {'K': 7.31, 'Ba': 3.0, 'Na': 2.31, 'Mg': 0.45, 'Ti': 9.03, 'Mn': 0.4, 'Nb': 12.56, 'Fe': 1.4, 'Si': 18.35, 'H': 1.05, 'O': 44.13}
			Density 2.88, Hardness 5.0, Elements {'K': 7.31, 'Ba': 3.0, 'Na': 2.31, 'Mg': 0.45, 'Ti': 9.03, 'Mn': 0.4, 'Nb': 12.56, 'Fe': 1.4, 'Si': 18.35, 'H': 1.05, 'O': 44.13}
	Found duplicates of "Neustadtelite", with these properties :
			Density None, Hardness 4.5, Elements {'Fe': 9.63, 'Co': 2.71, 'Bi': 48.04, 'As': 17.22, 'H': 0.34, 'O': 22.07}
			Density None, Hardness 4.5, Elements {'Fe': 9.63, 'Co': 2.71, 'Bi': 48.04, 'As': 17.22, 'H': 0.34, 'O': 22.07}
	Found duplicates of "Nevadaite", with these properties :
			Density 2.54, Hardness 3.0, Elements {'Al': 14.04, 'V': 2.84, 'Zn': 0.07, 'Cu': 7.23, 'P': 13.91, 'H': 2.58, 'O': 50.29, 'F': 9.04}
			Density 2.54, Hardness 3.0, Elements {'Al': 14.04, 'V': 2.84, 'Zn': 0.07, 'Cu': 7.23, 'P': 13.91, 'H': 2.58, 'O': 50.29, 'F': 9.04}
	Found duplicates of "Osmium", with these properties :
			Density 20.0, Hardness 6.5, Elements {'Ir': 25.2, 'Os': 74.8}
			Density 20.0, Hardness 6.5, Elements {'Ir': 25.2, 'Os': 74.8}
			Density 20.0, Hardness 6.5, Elements {'Ir': 25.2, 'Os': 74.8}
			Density 20.0, Hardness 6.5, Elements {'Ir': 25.2, 'Os': 74.8}
			Density 20.0, Hardness 6.5, Elements {'Ir': 25.2, 'Os': 74.8}
	Found duplicates of "Neyite", with these properties :
			Density 7.02, Hardness 2.5, Elements {'Cu': 2.88, 'Ag': 1.63, 'Bi': 41.04, 'Pb': 37.88, 'S': 16.57}
			Density 7.02, Hardness 2.5, Elements {'Cu': 2.88, 'Ag': 1.63, 'Bi': 41.04, 'Pb': 37.88, 'S': 16.57}
	Found duplicates of "Nezilovite", with these properties :
			Density 5.69, Hardness 4.5, Elements {'Ti': 2.0, 'Mn': 6.9, 'Zn': 10.94, 'Fe': 37.38, 'Pb': 17.34, 'O': 25.44}
			Density 5.69, Hardness 4.5, Elements {'Ti': 2.0, 'Mn': 6.9, 'Zn': 10.94, 'Fe': 37.38, 'Pb': 17.34, 'O': 25.44}
	Found duplicates of "Nickeline", with these properties :
			Density 7.79, Hardness 5.5, Elements {'Ni': 43.93, 'As': 56.07}
			Density 7.79, Hardness 5.5, Elements {'Ni': 43.93, 'As': 56.07}
	Found duplicates of "Nickelboussingaultite", with these properties :
			Density None, Hardness 2.5, Elements {'Mg': 1.57, 'Ni': 11.39, 'H': 5.22, 'S': 16.6, 'N': 7.25, 'O': 57.97}
			Density None, Hardness 2.5, Elements {'Mg': 1.57, 'Ni': 11.39, 'H': 5.22, 'S': 16.6, 'N': 7.25, 'O': 57.97}
	Found duplicates of "Nickelskutterudite", with these properties :
			Density 6.5, Hardness 5.75, Elements {'Co': 5.49, 'Ni': 16.39, 'As': 78.12}
			Density 6.5, Hardness 5.75, Elements {'Co': 5.49, 'Ni': 16.39, 'As': 78.12}
			Density 6.5, Hardness 5.75, Elements {'Co': 5.49, 'Ni': 16.39, 'As': 78.12}
	Found duplicates of "Nickelzippeite", with these properties :
			Density 4.3, Hardness 5.25, Elements {'U': 57.49, 'Ni': 4.73, 'H': 1.7, 'S': 3.87, 'O': 32.2}
			Density 4.3, Hardness 5.25, Elements {'U': 57.49, 'Ni': 4.73, 'H': 1.7, 'S': 3.87, 'O': 32.2}
	Found duplicates of "Nickelbischofite", with these properties :
			Density 1.929, Hardness 1.5, Elements {'Ni': 24.69, 'H': 5.09, 'Cl': 29.83, 'O': 40.39}
			Density 1.929, Hardness 1.5, Elements {'Ni': 24.69, 'H': 5.09, 'Cl': 29.83, 'O': 40.39}
	Found duplicates of "Nickelblodite", with these properties :
			Density 2.43, Hardness 3.5, Elements {'Na': 12.76, 'Mg': 1.69, 'Ni': 12.22, 'H': 2.24, 'S': 17.8, 'O': 53.29}
			Density 2.43, Hardness 3.5, Elements {'Na': 12.76, 'Mg': 1.69, 'Ni': 12.22, 'H': 2.24, 'S': 17.8, 'O': 53.29}
	Found duplicates of "Bravoite", with these properties :
			Density 5.01, Hardness 6.5, Elements {'Fe': 32.35, 'Co': 4.88, 'Ni': 9.71, 'S': 53.06}
			Density 5.01, Hardness 6.5, Elements {'Fe': 32.35, 'Co': 4.88, 'Ni': 9.71, 'S': 53.06}
	Found duplicates of "Polydymite", with these properties :
			Density 4.65, Hardness 5.0, Elements {'Ni': 57.85, 'S': 42.15}
			Density 4.65, Hardness 5.0, Elements {'Ni': 57.85, 'S': 42.15}
	Found duplicates of "Nickellotharmeyerite", with these properties :
			Density None, Hardness 4.5, Elements {'Ca': 6.39, 'Fe': 8.9, 'Co': 3.52, 'Ni': 10.52, 'Bi': 8.33, 'As': 29.86, 'H': 0.6, 'O': 31.88}
			Density None, Hardness 4.5, Elements {'Ca': 6.39, 'Fe': 8.9, 'Co': 3.52, 'Ni': 10.52, 'Bi': 8.33, 'As': 29.86, 'H': 0.6, 'O': 31.88}
	Found duplicates of "Nickelphosphide", with these properties :
			Density None, Hardness 6.75, Elements {'Fe': 35.7, 'Ni': 49.07, 'P': 15.23}
			Density None, Hardness 6.75, Elements {'Fe': 35.7, 'Ni': 49.07, 'P': 15.23}
	Found duplicates of "Nickelschneebergite", with these properties :
			Density None, Hardness 4.25, Elements {'Ca': 2.04, 'Fe': 1.9, 'Co': 6.01, 'Ni': 11.97, 'Bi': 24.87, 'As': 25.47, 'H': 0.53, 'O': 27.2}
			Density None, Hardness 4.25, Elements {'Ca': 2.04, 'Fe': 1.9, 'Co': 6.01, 'Ni': 11.97, 'Bi': 24.87, 'As': 25.47, 'H': 0.53, 'O': 27.2}
	Found duplicates of "Nickeltalmessite", with these properties :
			Density None, Hardness None, Elements {'Ca': 17.71, 'Ni': 12.96, 'As': 33.1, 'H': 0.89, 'O': 35.34}
			Density None, Hardness None, Elements {'Ca': 17.71, 'Ni': 12.96, 'As': 33.1, 'H': 0.89, 'O': 35.34}
	Found duplicates of "Nickenichite", with these properties :
			Density None, Hardness 3.0, Elements {'Na': 3.35, 'Ca': 2.19, 'Mg': 10.61, 'Al': 0.49, 'Fe': 4.06, 'Cu': 3.47, 'As': 40.9, 'O': 34.93}
			Density None, Hardness 3.0, Elements {'Na': 3.35, 'Ca': 2.19, 'Mg': 10.61, 'Al': 0.49, 'Fe': 4.06, 'Cu': 3.47, 'As': 40.9, 'O': 34.93}
	Found duplicates of "Aragonite", with these properties :
			Density 2.93, Hardness 3.75, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
			Density 2.93, Hardness 3.75, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
			Density 2.93, Hardness 3.75, Elements {'Ca': 40.04, 'C': 12.0, 'O': 47.96}
	Found duplicates of "Niedermayrite", with these properties :
			Density 3.36, Hardness None, Elements {'Cd': 15.34, 'Cu': 34.69, 'H': 1.93, 'S': 8.75, 'O': 39.3}
			Density 3.36, Hardness None, Elements {'Cd': 15.34, 'Cu': 34.69, 'H': 1.93, 'S': 8.75, 'O': 39.3}
	Found duplicates of "Nielsbohrite", with these properties :
			Density None, Hardness 2.0, Elements {'K': 1.54, 'U': 68.8, 'As': 6.76, 'H': 0.55, 'O': 22.34}
			Density None, Hardness 2.0, Elements {'K': 1.54, 'U': 68.8, 'As': 6.76, 'H': 0.55, 'O': 22.34}
	Found duplicates of "Nielsenite", with these properties :
			Density None, Hardness None, Elements {'Fe': 2.38, 'Cu': 67.35, 'Pd': 30.27}
			Density None, Hardness None, Elements {'Fe': 2.38, 'Cu': 67.35, 'Pd': 30.27}
	Found duplicates of "Nierite", with these properties :
			Density 3.17, Hardness 9.0, Elements {'Si': 60.06, 'N': 39.94}
			Density 3.17, Hardness 9.0, Elements {'Si': 60.06, 'N': 39.94}
	Found duplicates of "Ferronigerite-2N1S", with these properties :
			Density 4.51, Hardness 8.5, Elements {'Mg': 0.7, 'Al': 27.21, 'Zn': 3.77, 'Fe': 10.73, 'Sn': 20.53, 'H': 0.17, 'O': 36.89}
			Density 4.51, Hardness 8.5, Elements {'Mg': 0.7, 'Al': 27.21, 'Zn': 3.77, 'Fe': 10.73, 'Sn': 20.53, 'H': 0.17, 'O': 36.89}
			Density 4.51, Hardness 8.5, Elements {'Mg': 0.7, 'Al': 27.21, 'Zn': 3.77, 'Fe': 10.73, 'Sn': 20.53, 'H': 0.17, 'O': 36.89}
			Density 4.51, Hardness 8.5, Elements {'Mg': 0.7, 'Al': 27.21, 'Zn': 3.77, 'Fe': 10.73, 'Sn': 20.53, 'H': 0.17, 'O': 36.89}
	Found duplicates of "Ferronigerite-6N6S", with these properties :
			Density 4.51, Hardness 8.5, Elements {'Ca': 0.85, 'Mn': 0.39, 'Al': 27.95, 'Zn': 6.45, 'Fe': 11.02, 'Si': 0.4, 'Sn': 16.73, 'H': 0.14, 'O': 36.08}
			Density 4.51, Hardness 8.5, Elements {'Ca': 0.85, 'Mn': 0.39, 'Al': 27.95, 'Zn': 6.45, 'Fe': 11.02, 'Si': 0.4, 'Sn': 16.73, 'H': 0.14, 'O': 36.08}
	Found duplicates of "Clinozoisite-Sr", with these properties :
			Density None, Hardness 5.25, Elements {'Sr': 12.63, 'Ca': 10.27, 'Al': 13.39, 'Fe': 5.03, 'Si': 16.86, 'H': 0.2, 'O': 41.62}
			Density None, Hardness 5.25, Elements {'Sr': 12.63, 'Ca': 10.27, 'Al': 13.39, 'Fe': 5.03, 'Si': 16.86, 'H': 0.2, 'O': 41.62}
			Density None, Hardness 5.25, Elements {'Sr': 12.63, 'Ca': 10.27, 'Al': 13.39, 'Fe': 5.03, 'Si': 16.86, 'H': 0.2, 'O': 41.62}
	Found duplicates of "Nikischerite", with these properties :
			Density 2.33, Hardness 2.0, Elements {'Na': 1.78, 'Al': 6.95, 'Fe': 31.63, 'H': 3.63, 'S': 4.95, 'O': 51.07}
			Density 2.33, Hardness 2.0, Elements {'Na': 1.78, 'Al': 6.95, 'Fe': 31.63, 'H': 3.63, 'S': 4.95, 'O': 51.07}
	Found duplicates of "Niksergievite", with these properties :
			Density 3.16, Hardness 1.25, Elements {'K': 0.11, 'Ba': 24.13, 'Ca': 3.6, 'Mg': 0.24, 'Al': 13.03, 'Fe': 0.15, 'Si': 13.29, 'H': 0.92, 'C': 1.65, 'O': 42.88}
			Density 3.16, Hardness 1.25, Elements {'K': 0.11, 'Ba': 24.13, 'Ca': 3.6, 'Mg': 0.24, 'Al': 13.03, 'Fe': 0.15, 'Si': 13.29, 'H': 0.92, 'C': 1.65, 'O': 42.88}
	Found duplicates of "Brindleyite", with these properties :
			Density 3.17, Hardness 2.75, Elements {'Mg': 1.43, 'Al': 15.86, 'Fe': 1.64, 'Si': 8.25, 'Ni': 29.32, 'H': 1.18, 'O': 42.31}
			Density 3.17, Hardness 2.75, Elements {'Mg': 1.43, 'Al': 15.86, 'Fe': 1.64, 'Si': 8.25, 'Ni': 29.32, 'H': 1.18, 'O': 42.31}
	Found duplicates of "Columbite-Fe", with these properties :
			Density 6.3, Hardness 6.0, Elements {'Nb': 55.03, 'Fe': 16.54, 'O': 28.43}
			Density 6.3, Hardness 6.0, Elements {'Nb': 55.03, 'Fe': 16.54, 'O': 28.43}
			Density 6.3, Hardness 6.0, Elements {'Nb': 55.03, 'Fe': 16.54, 'O': 28.43}
	Found duplicates of "Nioboaeschynite-Ce", with these properties :
			Density 5.04, Hardness 5.449999999999999, Elements {'Ca': 3.27, 'Ce': 22.87, 'Ti': 3.26, 'Nb': 44.22, 'H': 0.27, 'O': 26.11}
			Density 5.04, Hardness 5.449999999999999, Elements {'Ca': 3.27, 'Ce': 22.87, 'Ti': 3.26, 'Nb': 44.22, 'H': 0.27, 'O': 26.11}
	Found duplicates of "Nioboaeschynite-Nd", with these properties :
			Density None, Hardness 5.5, Elements {'Ce': 13.53, 'Ti': 2.89, 'Nb': 39.26, 'H': 0.24, 'Nd': 20.9, 'O': 23.18}
			Density None, Hardness 5.5, Elements {'Ce': 13.53, 'Ti': 2.89, 'Nb': 39.26, 'H': 0.24, 'Nd': 20.9, 'O': 23.18}
	Found duplicates of "Nioboaeschynite-Y", with these properties :
			Density 5.34, Hardness 5.5, Elements {'Ca': 3.37, 'RE': 13.28, 'Y': 4.58, 'Th': 11.33, 'U': 0.58, 'Ta': 3.44, 'Ti': 11.95, 'Mn': 0.09, 'Nb': 23.69, 'Fe': 1.67, 'O': 26.04}
			Density 5.34, Hardness 5.5, Elements {'Ca': 3.37, 'RE': 13.28, 'Y': 4.58, 'Th': 11.33, 'U': 0.58, 'Ta': 3.44, 'Ti': 11.95, 'Mn': 0.09, 'Nb': 23.69, 'Fe': 1.67, 'O': 26.04}
			Density 5.34, Hardness 5.5, Elements {'Ca': 3.37, 'RE': 13.28, 'Y': 4.58, 'Th': 11.33, 'U': 0.58, 'Ta': 3.44, 'Ti': 11.95, 'Mn': 0.09, 'Nb': 23.69, 'Fe': 1.67, 'O': 26.04}
	Found duplicates of "Niobocarbide", with these properties :
			Density 10.25, Hardness 8.0, Elements {'Ta': 59.01, 'Nb': 32.83, 'C': 8.16}
			Density 10.25, Hardness 8.0, Elements {'Ta': 59.01, 'Nb': 32.83, 'C': 8.16}
	Found duplicates of "Niobokupletskite", with these properties :
			Density 3.325, Hardness 3.5, Elements {'K': 5.68, 'Na': 2.34, 'Zr': 2.65, 'Ti': 1.04, 'Mn': 21.54, 'Nb': 8.77, 'Zn': 3.32, 'Fe': 2.03, 'Si': 16.31, 'H': 0.29, 'O': 35.89, 'F': 0.14}
			Density 3.325, Hardness 3.5, Elements {'K': 5.68, 'Na': 2.34, 'Zr': 2.65, 'Ti': 1.04, 'Mn': 21.54, 'Nb': 8.77, 'Zn': 3.32, 'Fe': 2.03, 'Si': 16.31, 'H': 0.29, 'O': 35.89, 'F': 0.14}
	Found duplicates of "Nisbite", with these properties :
			Density 8.0, Hardness 5.0, Elements {'Ni': 19.42, 'Sb': 80.58}
			Density 8.0, Hardness 5.0, Elements {'Ni': 19.42, 'Sb': 80.58}
	Found duplicates of "Niter", with these properties :
			Density 2.1, Hardness 2.0, Elements {'K': 38.67, 'N': 13.85, 'O': 47.47}
			Density 2.1, Hardness 2.0, Elements {'K': 38.67, 'N': 13.85, 'O': 47.47}
			Density 2.1, Hardness 2.0, Elements {'K': 38.67, 'N': 13.85, 'O': 47.47}
	Found duplicates of "Gwihabaite", with these properties :
			Density 1.77, Hardness 5.0, Elements {'K': 11.46, 'H': 3.54, 'N': 28.73, 'O': 56.26}
			Density 1.77, Hardness 5.0, Elements {'K': 11.46, 'H': 3.54, 'N': 28.73, 'O': 56.26}
			Density 1.77, Hardness 5.0, Elements {'K': 11.46, 'H': 3.54, 'N': 28.73, 'O': 56.26}
	Found duplicates of "Nitratine", with these properties :
			Density 2.26, Hardness 1.75, Elements {'Na': 27.05, 'N': 16.48, 'O': 56.47}
			Density 2.26, Hardness 1.75, Elements {'Na': 27.05, 'N': 16.48, 'O': 56.47}
			Density 2.26, Hardness 1.75, Elements {'Na': 27.05, 'N': 16.48, 'O': 56.47}
			Density 2.26, Hardness 1.75, Elements {'Na': 27.05, 'N': 16.48, 'O': 56.47}
	Found duplicates of "Niveolanite", with these properties :
			Density None, Hardness None, Elements {'Na': 15.1, 'Ca': 2.8, 'Be': 6.17, 'H': 3.11, 'C': 8.4, 'O': 64.41}
			Density None, Hardness None, Elements {'Na': 15.1, 'Ca': 2.8, 'Be': 6.17, 'H': 3.11, 'C': 8.4, 'O': 64.41}
	Found duplicates of "Noelbensonite", with these properties :
			Density 3.87, Hardness 4.0, Elements {'Ba': 29.38, 'Mn': 23.51, 'Si': 12.02, 'H': 0.86, 'O': 34.23}
			Density 3.87, Hardness 4.0, Elements {'Ba': 29.38, 'Mn': 23.51, 'Si': 12.02, 'H': 0.86, 'O': 34.23}
	Found duplicates of "Nontronite", with these properties :
			Density 2.3, Hardness 1.75, Elements {'Na': 1.39, 'Al': 5.44, 'Fe': 22.52, 'Si': 16.99, 'H': 2.03, 'O': 51.62}
			Density 2.3, Hardness 1.75, Elements {'Na': 1.39, 'Al': 5.44, 'Fe': 22.52, 'Si': 16.99, 'H': 2.03, 'O': 51.62}
	Found duplicates of "Nordenskioldine", with these properties :
			Density 4.2, Hardness 5.75, Elements {'Ca': 14.5, 'Sn': 42.95, 'B': 7.82, 'O': 34.73}
			Density 4.2, Hardness 5.75, Elements {'Ca': 14.5, 'Sn': 42.95, 'B': 7.82, 'O': 34.73}
	Found duplicates of "Nordstromite", with these properties :
			Density None, Hardness 2.25, Elements {'Cu': 2.28, 'Bi': 52.54, 'Pb': 22.32, 'Se': 11.34, 'S': 11.52}
			Density None, Hardness 2.25, Elements {'Cu': 2.28, 'Bi': 52.54, 'Pb': 22.32, 'Se': 11.34, 'S': 11.52}
	Found duplicates of "Normandite", with these properties :
			Density 3.49, Hardness 5.5, Elements {'Na': 5.96, 'Ca': 10.39, 'Zr': 2.37, 'Ti': 7.45, 'Mn': 10.68, 'Nb': 7.23, 'Fe': 3.62, 'Si': 14.57, 'O': 35.27, 'F': 2.46}
			Density 3.49, Hardness 5.5, Elements {'Na': 5.96, 'Ca': 10.39, 'Zr': 2.37, 'Ti': 7.45, 'Mn': 10.68, 'Nb': 7.23, 'Fe': 3.62, 'Si': 14.57, 'O': 35.27, 'F': 2.46}
	Found duplicates of "Nosean", with these properties :
			Density 2.34, Hardness 5.75, Elements {'Na': 18.17, 'Al': 15.99, 'Si': 16.65, 'H': 0.2, 'S': 3.17, 'O': 45.83}
			Density 2.34, Hardness 5.75, Elements {'Na': 18.17, 'Al': 15.99, 'Si': 16.65, 'H': 0.2, 'S': 3.17, 'O': 45.83}
	Found duplicates of "Novgorodovaite", with these properties :
			Density 2.38, Hardness 2.5, Elements {'Ca': 28.77, 'H': 1.66, 'C': 8.62, 'Cl': 24.18, 'O': 36.76}
			Density 2.38, Hardness 2.5, Elements {'Ca': 28.77, 'H': 1.66, 'C': 8.62, 'Cl': 24.18, 'O': 36.76}
	Found duplicates of "Novodneprite", with these properties :
			Density None, Hardness None, Elements {'Pb': 75.94, 'Au': 24.06}
			Density None, Hardness None, Elements {'Pb': 75.94, 'Au': 24.06}
	Found duplicates of "Nuffieldite", with these properties :
			Density 7.01, Hardness 3.75, Elements {'Cu': 4.78, 'Bi': 39.33, 'Pb': 38.99, 'S': 16.9}
			Density 7.01, Hardness 3.75, Elements {'Cu': 4.78, 'Bi': 39.33, 'Pb': 38.99, 'S': 16.9}
	Found duplicates of "Numanoite", with these properties :
			Density 2.96, Hardness 4.5, Elements {'Ca': 27.26, 'Mg': 0.61, 'Zn': 0.41, 'Cu': 8.58, 'B': 7.49, 'H': 1.09, 'C': 4.31, 'O': 50.25}
			Density 2.96, Hardness 4.5, Elements {'Ca': 27.26, 'Mg': 0.61, 'Zn': 0.41, 'Cu': 8.58, 'B': 7.49, 'H': 1.09, 'C': 4.31, 'O': 50.25}
	Found duplicates of "Phosphohedyphane", with these properties :
			Density None, Hardness 4.0, Elements {'Ca': 6.57, 'As': 1.68, 'P': 8.0, 'Pb': 62.46, 'Cl': 3.32, 'O': 17.97}
			Density None, Hardness 4.0, Elements {'Ca': 6.57, 'As': 1.68, 'P': 8.0, 'Pb': 62.46, 'Cl': 3.32, 'O': 17.97}
			Density None, Hardness None, Elements {'Ca': 6.57, 'As': 1.68, 'P': 8.0, 'Pb': 62.46, 'Cl': 3.32, 'O': 17.97}
			Density None, Hardness 4.0, Elements {'Ca': 6.57, 'As': 1.68, 'P': 8.0, 'Pb': 62.46, 'Cl': 3.32, 'O': 17.97}
	Found duplicates of "Nyerereite", with these properties :
			Density 2.541, Hardness None, Elements {'Na': 22.31, 'Ca': 19.45, 'C': 11.66, 'O': 46.58}
			Density 2.541, Hardness None, Elements {'Na': 22.31, 'Ca': 19.45, 'C': 11.66, 'O': 46.58}
	Found duplicates of "Obertiite", with these properties :
			Density None, Hardness 5.0, Elements {'Na': 8.06, 'Mg': 8.53, 'Ti': 5.6, 'Fe': 6.53, 'Si': 26.27, 'H': 0.03, 'O': 44.43, 'F': 0.56}
			Density None, Hardness 5.0, Elements {'Na': 8.06, 'Mg': 8.53, 'Ti': 5.6, 'Fe': 6.53, 'Si': 26.27, 'H': 0.03, 'O': 44.43, 'F': 0.56}
			Density None, Hardness 5.0, Elements {'Na': 8.06, 'Mg': 8.53, 'Ti': 5.6, 'Fe': 6.53, 'Si': 26.27, 'H': 0.03, 'O': 44.43, 'F': 0.56}
	Found duplicates of "Yttropyrochlore-Y", with these properties :
			Density 3.7, Hardness 5.0, Elements {'Na': 1.01, 'Ca': 1.76, 'Y': 13.65, 'U': 10.44, 'Ta': 19.84, 'Ti': 2.1, 'Nb': 24.45, 'Fe': 1.22, 'H': 0.62, 'O': 24.91}
			Density 3.7, Hardness 5.0, Elements {'Na': 1.01, 'Ca': 1.76, 'Y': 13.65, 'U': 10.44, 'Ta': 19.84, 'Ti': 2.1, 'Nb': 24.45, 'Fe': 1.22, 'H': 0.62, 'O': 24.91}
	Found duplicates of "Anatase", with these properties :
			Density 3.9, Hardness 5.75, Elements {'Ti': 59.94, 'O': 40.06}
			Density 3.9, Hardness 5.75, Elements {'Ti': 59.94, 'O': 40.06}
	Found duplicates of "Odintsovite", with these properties :
			Density 2.96, Hardness 5.25, Elements {'K': 5.72, 'Na': 6.73, 'Ca': 8.79, 'Ti': 7.0, 'Be': 2.64, 'Si': 24.65, 'O': 44.47}
			Density 2.96, Hardness 5.25, Elements {'K': 5.72, 'Na': 6.73, 'Ca': 8.79, 'Ti': 7.0, 'Be': 2.64, 'Si': 24.65, 'O': 44.47}
	Found duplicates of "Ganterite", with these properties :
			Density None, Hardness 4.25, Elements {'K': 2.5, 'Ba': 13.78, 'Na': 1.42, 'Mg': 0.5, 'Ti': 0.44, 'Al': 19.2, 'Fe': 0.51, 'Si': 17.43, 'H': 0.43, 'O': 43.8}
			Density None, Hardness 4.25, Elements {'K': 2.5, 'Ba': 13.78, 'Na': 1.42, 'Mg': 0.5, 'Ti': 0.44, 'Al': 19.2, 'Fe': 0.51, 'Si': 17.43, 'H': 0.43, 'O': 43.8}
			Density None, Hardness 4.25, Elements {'K': 2.5, 'Ba': 13.78, 'Na': 1.42, 'Mg': 0.5, 'Ti': 0.44, 'Al': 19.2, 'Fe': 0.51, 'Si': 17.43, 'H': 0.43, 'O': 43.8}
			Density None, Hardness 4.25, Elements {'K': 2.5, 'Ba': 13.78, 'Na': 1.42, 'Mg': 0.5, 'Ti': 0.44, 'Al': 19.2, 'Fe': 0.51, 'Si': 17.43, 'H': 0.43, 'O': 43.8}
	Found duplicates of "Oenite", with these properties :
			Density 7.92, Hardness 5.25, Elements {'Co': 23.06, 'Sb': 47.63, 'As': 29.31}
			Density 7.92, Hardness 5.25, Elements {'Co': 23.06, 'Sb': 47.63, 'As': 29.31}
	Found duplicates of "Orebroite-VIII", with these properties :
			Density None, Hardness 4.0, Elements {'Mn': 43.03, 'Fe': 7.29, 'Si': 7.33, 'Sb': 12.71, 'H': 0.39, 'O': 29.24}
			Density None, Hardness 4.0, Elements {'Mn': 43.03, 'Fe': 7.29, 'Si': 7.33, 'Sb': 12.71, 'H': 0.39, 'O': 29.24}
	Found duplicates of "Oftedalite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 3.94, 'Ca': 3.25, 'Y': 0.27, 'Sc': 4.44, 'Mn': 1.02, 'Be': 2.7, 'Al': 0.25, 'Fe': 0.23, 'Si': 34.58, 'O': 49.33}
			Density None, Hardness 6.0, Elements {'K': 3.94, 'Ca': 3.25, 'Y': 0.27, 'Sc': 4.44, 'Mn': 1.02, 'Be': 2.7, 'Al': 0.25, 'Fe': 0.23, 'Si': 34.58, 'O': 49.33}
	Found duplicates of "Okayamalite", with these properties :
			Density 3.3, Hardness 5.5, Elements {'Ca': 33.14, 'Si': 11.61, 'B': 8.94, 'O': 46.31}
			Density 3.3, Hardness 5.5, Elements {'Ca': 33.14, 'Si': 11.61, 'B': 8.94, 'O': 46.31}
	Found duplicates of "Olekminskite", with these properties :
			Density 3.7, Hardness 3.0, Elements {'Ba': 4.8, 'Sr': 49.02, 'Ca': 4.2, 'C': 8.4, 'O': 33.57}
			Density 3.7, Hardness 3.0, Elements {'Ba': 4.8, 'Sr': 49.02, 'Ca': 4.2, 'C': 8.4, 'O': 33.57}
	Found duplicates of "Oligoclase", with these properties :
			Density 2.65, Hardness 7.0, Elements {'Na': 6.93, 'Ca': 3.02, 'Al': 12.2, 'Si': 29.63, 'O': 48.22}
			Density 2.65, Hardness 7.0, Elements {'Na': 6.93, 'Ca': 3.02, 'Al': 12.2, 'Si': 29.63, 'O': 48.22}
	Found duplicates of "Olivine", with these properties :
			Density 3.32, Hardness 6.75, Elements {'Mg': 25.37, 'Fe': 14.57, 'Si': 18.32, 'O': 41.74}
			Density 3.32, Hardness 6.75, Elements {'Mg': 25.37, 'Fe': 14.57, 'Si': 18.32, 'O': 41.74}
			Density 3.32, Hardness 6.75, Elements {'Mg': 25.37, 'Fe': 14.57, 'Si': 18.32, 'O': 41.74}
			Density 3.32, Hardness 6.75, Elements {'Mg': 25.37, 'Fe': 14.57, 'Si': 18.32, 'O': 41.74}
	Found duplicates of "Olkhonskite", with these properties :
			Density 4.48, Hardness 8.0, Elements {'Ti': 36.73, 'V': 6.51, 'Cr': 19.94, 'O': 36.82}
			Density 4.48, Hardness 8.0, Elements {'Ti': 36.73, 'V': 6.51, 'Cr': 19.94, 'O': 36.82}
	Found duplicates of "Olmiite", with these properties :
			Density 3.05, Hardness 5.25, Elements {'Ca': 22.65, 'Mn': 22.95, 'Fe': 0.27, 'Si': 13.81, 'H': 0.99, 'O': 39.32}
			Density 3.05, Hardness 5.25, Elements {'Ca': 22.65, 'Mn': 22.95, 'Fe': 0.27, 'Si': 13.81, 'H': 0.99, 'O': 39.32}
	Found duplicates of "Olsacherite", with these properties :
			Density 6.55, Hardness 3.25, Elements {'Pb': 63.42, 'Se': 12.08, 'S': 4.91, 'O': 19.59}
			Density 6.55, Hardness 3.25, Elements {'Pb': 63.42, 'Se': 12.08, 'S': 4.91, 'O': 19.59}
	Found duplicates of "Ominelite", with these properties :
			Density None, Hardness 7.0, Elements {'Mg': 0.77, 'Al': 25.57, 'Fe': 15.88, 'Si': 8.87, 'B': 3.42, 'O': 45.49}
			Density None, Hardness 7.0, Elements {'Mg': 0.77, 'Al': 25.57, 'Fe': 15.88, 'Si': 8.87, 'B': 3.42, 'O': 45.49}
	Found duplicates of "Omongwaite", with these properties :
			Density None, Hardness None, Elements {'K': 2.62, 'Na': 3.84, 'Ca': 21.67, 'H': 0.69, 'S': 22.11, 'O': 49.07}
			Density None, Hardness None, Elements {'K': 2.62, 'Na': 3.84, 'Ca': 21.67, 'H': 0.69, 'S': 22.11, 'O': 49.07}
	Found duplicates of "Oneillite", with these properties :
			Density 3.2, Hardness 5.5, Elements {'Na': 11.02, 'Ca': 3.84, 'Zr': 8.75, 'Mn': 5.27, 'Nb': 2.97, 'Fe': 5.36, 'Si': 22.44, 'H': 0.1, 'Cl': 0.57, 'O': 39.68}
			Density 3.2, Hardness 5.5, Elements {'Na': 11.02, 'Ca': 3.84, 'Zr': 8.75, 'Mn': 5.27, 'Nb': 2.97, 'Fe': 5.36, 'Si': 22.44, 'H': 0.1, 'Cl': 0.57, 'O': 39.68}
	Found duplicates of "Opal", with these properties :
			Density 2.09, Hardness 5.75, Elements {'Si': 32.24, 'H': 3.47, 'O': 64.29}
			Density 2.09, Hardness 5.75, Elements {'Si': 32.24, 'H': 3.47, 'O': 64.29}
			Density 2.09, Hardness 5.75, Elements {'Si': 32.24, 'H': 3.47, 'O': 64.29}
			Density 2.09, Hardness 5.75, Elements {'Si': 32.24, 'H': 3.47, 'O': 64.29}
			Density 2.09, Hardness 5.75, Elements {'Si': 32.24, 'H': 3.47, 'O': 64.29}
	Found duplicates of "Renierite", with these properties :
			Density 4.38, Hardness 4.5, Elements {'Zn': 21.72, 'Fe': 13.49, 'Cu': 24.95, 'Ge': 6.58, 'As': 2.26, 'S': 30.99}
			Density 4.38, Hardness 4.5, Elements {'Zn': 21.72, 'Fe': 13.49, 'Cu': 24.95, 'Ge': 6.58, 'As': 2.26, 'S': 30.99}
	Found duplicates of "Thorite", with these properties :
			Density 5.35, Hardness 5.0, Elements {'Th': 71.59, 'Si': 8.67, 'O': 19.74}
			Density 5.35, Hardness 5.0, Elements {'Th': 71.59, 'Si': 8.67, 'O': 19.74}
	Found duplicates of "Organovaite-Mn", with these properties :
			Density 2.88, Hardness 5.0, Elements {'K': 3.79, 'Ba': 1.16, 'Na': 0.39, 'Ca': 0.84, 'Ti': 5.65, 'Mn': 4.17, 'Nb': 20.76, 'Al': 0.11, 'Zn': 1.65, 'Fe': 0.24, 'Si': 18.83, 'H': 0.59, 'O': 41.82}
			Density 2.88, Hardness 5.0, Elements {'K': 3.79, 'Ba': 1.16, 'Na': 0.39, 'Ca': 0.84, 'Ti': 5.65, 'Mn': 4.17, 'Nb': 20.76, 'Al': 0.11, 'Zn': 1.65, 'Fe': 0.24, 'Si': 18.83, 'H': 0.59, 'O': 41.82}
	Found duplicates of "Organovaite-Zn", with these properties :
			Density 2.88, Hardness 5.0, Elements {'K': 3.05, 'Ba': 2.25, 'Na': 0.47, 'Ca': 0.82, 'Ti': 6.28, 'Mn': 0.45, 'Nb': 18.28, 'Al': 0.22, 'Zn': 4.29, 'Fe': 0.23, 'Si': 18.19, 'H': 1.12, 'O': 44.34}
			Density 2.88, Hardness 5.0, Elements {'K': 3.05, 'Ba': 2.25, 'Na': 0.47, 'Ca': 0.82, 'Ti': 6.28, 'Mn': 0.45, 'Nb': 18.28, 'Al': 0.22, 'Zn': 4.29, 'Fe': 0.23, 'Si': 18.19, 'H': 1.12, 'O': 44.34}
	Found duplicates of "Orlandiite", with these properties :
			Density None, Hardness None, Elements {'H': 0.28, 'Pb': 69.13, 'Se': 8.78, 'Cl': 13.8, 'O': 8.01}
			Density None, Hardness None, Elements {'H': 0.28, 'Pb': 69.13, 'Se': 8.78, 'Cl': 13.8, 'O': 8.01}
	Found duplicates of "Orlovite", with these properties :
			Density None, Hardness None, Elements {'K': 9.74, 'Li': 1.73, 'Ti': 11.93, 'Si': 28.0, 'O': 43.86, 'F': 4.73}
			Density None, Hardness None, Elements {'K': 9.74, 'Li': 1.73, 'Ti': 11.93, 'Si': 28.0, 'O': 43.86, 'F': 4.73}
	Found duplicates of "Orlymanite", with these properties :
			Density 2.75, Hardness 4.5, Elements {'Ca': 15.91, 'Mn': 16.35, 'Si': 22.29, 'H': 1.0, 'O': 44.45}
			Density 2.75, Hardness 4.5, Elements {'Ca': 15.91, 'Mn': 16.35, 'Si': 22.29, 'H': 1.0, 'O': 44.45}
	Found duplicates of "Gold", with these properties :
			Density 17.64, Hardness 2.75, Elements {'Au': 100.0}
			Density 17.64, Hardness 2.75, Elements {'Au': 100.0}
	Found duplicates of "Orschallite", with these properties :
			Density 1.9, Hardness 4.0, Elements {'Ca': 20.29, 'H': 4.08, 'S': 16.23, 'O': 59.4}
			Density 1.9, Hardness 4.0, Elements {'Ca': 20.29, 'H': 4.08, 'S': 16.23, 'O': 59.4}
	Found duplicates of "Allanite-Y", with these properties :
			Density 3.75, Hardness 5.5, Elements {'Ca': 2.01, 'Ce': 14.04, 'Y': 17.82, 'Al': 10.14, 'Fe': 7.0, 'Si': 14.07, 'H': 0.17, 'O': 34.74}
			Density 3.75, Hardness 5.5, Elements {'Ca': 2.01, 'Ce': 14.04, 'Y': 17.82, 'Al': 10.14, 'Fe': 7.0, 'Si': 14.07, 'H': 0.17, 'O': 34.74}
	Found duplicates of "Lizardite", with these properties :
			Density 2.57, Hardness 2.5, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
			Density 2.57, Hardness 2.5, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
	Found duplicates of "Orthoclase", with these properties :
			Density 2.56, Hardness 6.0, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
			Density 2.56, Hardness 6.0, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
			Density 2.56, Hardness 6.0, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
	Found duplicates of "Enstatite", with these properties :
			Density 3.2, Hardness 5.5, Elements {'Mg': 24.21, 'Si': 27.98, 'O': 47.81}
			Density 3.2, Hardness 5.5, Elements {'Mg': 24.21, 'Si': 27.98, 'O': 47.81}
			Density 3.2, Hardness 5.5, Elements {'Mg': 24.21, 'Si': 27.98, 'O': 47.81}
	Found duplicates of "Ferrosilite", with these properties :
			Density 3.95, Hardness 5.5, Elements {'Mg': 10.46, 'Fe': 24.04, 'Si': 24.18, 'O': 41.32}
			Density 3.95, Hardness 5.5, Elements {'Mg': 10.46, 'Fe': 24.04, 'Si': 24.18, 'O': 41.32}
	Found duplicates of "Orthominasragrite", with these properties :
			Density None, Hardness 1.0, Elements {'V': 20.13, 'H': 3.98, 'S': 12.67, 'O': 63.22}
			Density None, Hardness 1.0, Elements {'V': 20.13, 'H': 3.98, 'S': 12.67, 'O': 63.22}
			Density None, Hardness 1.0, Elements {'V': 20.13, 'H': 3.98, 'S': 12.67, 'O': 63.22}
	Found duplicates of "Orthowalpurgite", with these properties :
			Density 6.5, Hardness 4.5, Elements {'U': 16.04, 'Bi': 56.34, 'As': 10.1, 'H': 0.27, 'O': 17.25}
			Density 6.5, Hardness 4.5, Elements {'U': 16.04, 'Bi': 56.34, 'As': 10.1, 'H': 0.27, 'O': 17.25}
	Found duplicates of "Iridium", with these properties :
			Density 22.7, Hardness 6.5, Elements {'Ir': 52.58, 'Os': 31.22, 'Ru': 5.53, 'Pt': 10.67}
			Density 22.7, Hardness 6.5, Elements {'Ir': 52.58, 'Os': 31.22, 'Ru': 5.53, 'Pt': 10.67}
			Density 22.7, Hardness 6.5, Elements {'Ir': 52.58, 'Os': 31.22, 'Ru': 5.53, 'Pt': 10.67}
			Density 22.7, Hardness 6.5, Elements {'Ir': 52.58, 'Os': 31.22, 'Ru': 5.53, 'Pt': 10.67}
			Density 22.7, Hardness 6.5, Elements {'Ir': 52.58, 'Os': 31.22, 'Ru': 5.53, 'Pt': 10.67}
	Found duplicates of "Osumilite-Fe", with these properties :
			Density 2.64, Hardness 5.5, Elements {'K': 2.8, 'Na': 0.55, 'Mg': 1.16, 'Al': 13.53, 'Fe': 12.0, 'Si': 24.14, 'O': 45.83}
			Density 2.64, Hardness 5.5, Elements {'K': 2.8, 'Na': 0.55, 'Mg': 1.16, 'Al': 13.53, 'Fe': 12.0, 'Si': 24.14, 'O': 45.83}
	Found duplicates of "Oswaldpeetersite", with these properties :
			Density None, Hardness 2.5, Elements {'U': 67.42, 'H': 1.43, 'C': 1.7, 'O': 29.45}
			Density None, Hardness 2.5, Elements {'U': 67.42, 'H': 1.43, 'C': 1.7, 'O': 29.45}
	Found duplicates of "Ottensite", with these properties :
			Density None, Hardness 3.5, Elements {'K': 0.1, 'Na': 5.47, 'Sb': 70.08, 'H': 0.53, 'S': 7.37, 'O': 16.46}
			Density None, Hardness 3.5, Elements {'K': 0.1, 'Na': 5.47, 'Sb': 70.08, 'H': 0.53, 'S': 7.37, 'O': 16.46}
	Found duplicates of "Oulankaite", with these properties :
			Density 10.27, Hardness 3.75, Elements {'Fe': 4.21, 'Cu': 14.36, 'Sn': 8.94, 'Te': 19.22, 'Pd': 30.06, 'Pt': 18.37, 'S': 4.83}
			Density 10.27, Hardness 3.75, Elements {'Fe': 4.21, 'Cu': 14.36, 'Sn': 8.94, 'Te': 19.22, 'Pd': 30.06, 'Pt': 18.37, 'S': 4.83}
			Density 10.27, Hardness 3.75, Elements {'Fe': 4.21, 'Cu': 14.36, 'Sn': 8.94, 'Te': 19.22, 'Pd': 30.06, 'Pt': 18.37, 'S': 4.83}
	Found duplicates of "Ovamboite", with these properties :
			Density None, Hardness 3.5, Elements {'V': 0.09, 'Zn': 3.28, 'Ga': 0.46, 'Fe': 4.67, 'Cu': 39.21, 'Sn': 0.03, 'Ge': 9.86, 'Mo': 1.0, 'As': 2.55, 'W': 9.7, 'S': 29.17}
			Density None, Hardness 3.5, Elements {'V': 0.09, 'Zn': 3.28, 'Ga': 0.46, 'Fe': 4.67, 'Cu': 39.21, 'Sn': 0.03, 'Ge': 9.86, 'Mo': 1.0, 'As': 2.55, 'W': 9.7, 'S': 29.17}
	Found duplicates of "Owensite", with these properties :
			Density 4.78, Hardness 3.5, Elements {'Ba': 16.41, 'Fe': 12.51, 'Cu': 28.47, 'Ni': 4.38, 'Pb': 12.38, 'S': 25.86}
			Density 4.78, Hardness 3.5, Elements {'Ba': 16.41, 'Fe': 12.51, 'Cu': 28.47, 'Ni': 4.38, 'Pb': 12.38, 'S': 25.86}
	Found duplicates of "Beraunite", with these properties :
			Density 2.9, Hardness 2.0, Elements {'Fe': 38.42, 'P': 14.21, 'H': 1.5, 'O': 45.87}
			Density 2.9, Hardness 2.0, Elements {'Fe': 38.42, 'P': 14.21, 'H': 1.5, 'O': 45.87}
	Found duplicates of "Ferrokaersutite", with these properties :
			Density 3.2, Hardness 5.5, Elements {'Na': 2.3, 'Ca': 8.02, 'Ti': 4.79, 'Al': 5.4, 'Fe': 22.36, 'Si': 16.87, 'H': 0.2, 'O': 40.04}
			Density 3.2, Hardness 5.5, Elements {'Na': 2.3, 'Ca': 8.02, 'Ti': 4.79, 'Al': 5.4, 'Fe': 22.36, 'Si': 16.87, 'H': 0.2, 'O': 40.04}
	Found duplicates of "Julgoldite-Fe+++", with these properties :
			Density 3.6, Hardness 4.5, Elements {'Ca': 14.57, 'Mg': 0.22, 'Al': 1.35, 'Fe': 27.16, 'Si': 15.32, 'H': 0.64, 'O': 40.73}
			Density 3.6, Hardness 4.5, Elements {'Ca': 14.57, 'Mg': 0.22, 'Al': 1.35, 'Fe': 27.16, 'Si': 15.32, 'H': 0.64, 'O': 40.73}
			Density 3.6, Hardness 4.5, Elements {'Ca': 14.57, 'Mg': 0.22, 'Al': 1.35, 'Fe': 27.16, 'Si': 15.32, 'H': 0.64, 'O': 40.73}
	Found duplicates of "Kaersutite", with these properties :
			Density 3.24, Hardness 5.5, Elements {'Na': 2.63, 'Ca': 9.18, 'Mg': 11.14, 'Ti': 5.49, 'Al': 6.18, 'Si': 19.31, 'H': 0.23, 'O': 45.83}
			Density 3.24, Hardness 5.5, Elements {'Na': 2.63, 'Ca': 9.18, 'Mg': 11.14, 'Ti': 5.49, 'Al': 6.18, 'Si': 19.31, 'H': 0.23, 'O': 45.83}
	Found duplicates of "Oxykinoshitalite", with these properties :
			Density 3.3, Hardness 2.5, Elements {'K': 3.31, 'Ba': 12.98, 'Na': 0.32, 'Ca': 0.08, 'Mg': 6.6, 'Ti': 7.26, 'Mn': 0.11, 'Al': 8.5, 'Fe': 9.79, 'Si': 13.05, 'H': 0.06, 'O': 36.98, 'F': 0.97}
			Density 3.3, Hardness 2.5, Elements {'K': 3.31, 'Ba': 12.98, 'Na': 0.32, 'Ca': 0.08, 'Mg': 6.6, 'Ti': 7.26, 'Mn': 0.11, 'Al': 8.5, 'Fe': 9.79, 'Si': 13.05, 'H': 0.06, 'O': 36.98, 'F': 0.97}
	Found duplicates of "Petscheckite", with these properties :
			Density 5.5, Hardness 5.0, Elements {'U': 36.52, 'Ta': 13.88, 'Nb': 21.38, 'Fe': 8.57, 'O': 19.64}
			Density 5.5, Hardness 5.0, Elements {'U': 36.52, 'Ta': 13.88, 'Nb': 21.38, 'Fe': 8.57, 'O': 19.64}
			Density 5.5, Hardness 5.0, Elements {'U': 36.52, 'Ta': 13.88, 'Nb': 21.38, 'Fe': 8.57, 'O': 19.64}
	Found duplicates of "Oxyvanite", with these properties :
			Density None, Hardness None, Elements {'V': 65.64, 'O': 34.36}
			Density None, Hardness None, Elements {'V': 65.64, 'O': 34.36}
	Found duplicates of "Paarite", with these properties :
			Density None, Hardness 3.5, Elements {'Fe': 0.03, 'Cu': 4.9, 'Bi': 60.77, 'Pb': 16.45, 'S': 17.85}
			Density None, Hardness 3.5, Elements {'Fe': 0.03, 'Cu': 4.9, 'Bi': 60.77, 'Pb': 16.45, 'S': 17.85}
	Found duplicates of "Paceite", with these properties :
			Density None, Hardness 1.5, Elements {'Ca': 8.95, 'Cu': 14.19, 'H': 5.4, 'C': 21.45, 'O': 50.01}
			Density None, Hardness 1.5, Elements {'Ca': 8.95, 'Cu': 14.19, 'H': 5.4, 'C': 21.45, 'O': 50.01}
	Found duplicates of "Padmaite", with these properties :
			Density None, Hardness 3.5, Elements {'Bi': 52.99, 'Pd': 26.99, 'Se': 20.02}
			Density None, Hardness 3.5, Elements {'Bi': 52.99, 'Pd': 26.99, 'Se': 20.02}
	Found duplicates of "Paganoite", with these properties :
			Density None, Hardness 1.5, Elements {'Co': 1.54, 'Ni': 12.01, 'Bi': 49.23, 'As': 18.18, 'O': 19.03}
			Density None, Hardness 1.5, Elements {'Co': 1.54, 'Ni': 12.01, 'Bi': 49.23, 'As': 18.18, 'O': 19.03}
	Found duplicates of "Pakhomovskyite", with these properties :
			Density 2.71, Hardness 2.0, Elements {'Mg': 1.83, 'Mn': 1.85, 'Fe': 0.33, 'Co': 27.82, 'Ni': 0.47, 'P': 12.35, 'H': 3.34, 'O': 52.01}
			Density 2.71, Hardness 2.0, Elements {'Mg': 1.83, 'Mn': 1.85, 'Fe': 0.33, 'Co': 27.82, 'Ni': 0.47, 'P': 12.35, 'H': 3.34, 'O': 52.01}
	Found duplicates of "Palladodymite", with these properties :
			Density None, Hardness None, Elements {'As': 26.31, 'Pd': 42.98, 'Rh': 30.72}
			Density None, Hardness None, Elements {'As': 26.31, 'Pd': 42.98, 'Rh': 30.72}
	Found duplicates of "Palygorskite", with these properties :
			Density 2.15, Hardness 2.25, Elements {'Mg': 8.86, 'Al': 3.28, 'Si': 27.31, 'H': 2.21, 'O': 58.34}
			Density 2.15, Hardness 2.25, Elements {'Mg': 8.86, 'Al': 3.28, 'Si': 27.31, 'H': 2.21, 'O': 58.34}
	Found duplicates of "Kesterite", with these properties :
			Density 4.56, Hardness 4.5, Elements {'Zn': 10.38, 'Fe': 2.95, 'Cu': 26.89, 'Sn': 32.65, 'S': 27.14}
			Density 4.56, Hardness 4.5, Elements {'Zn': 10.38, 'Fe': 2.95, 'Cu': 26.89, 'Sn': 32.65, 'S': 27.14}
	Found duplicates of "Priceite", with these properties :
			Density 2.42, Hardness 3.25, Elements {'Ca': 22.95, 'B': 15.48, 'H': 2.02, 'O': 59.55}
			Density 2.42, Hardness 3.25, Elements {'Ca': 22.95, 'B': 15.48, 'H': 2.02, 'O': 59.55}
	Found duplicates of "Panichiite", with these properties :
			Density 2.43, Hardness None, Elements {'K': 1.05, 'Sn': 31.6, 'H': 2.06, 'Br': 1.5, 'N': 7.16, 'Cl': 56.63}
			Density 2.43, Hardness None, Elements {'K': 1.05, 'Sn': 31.6, 'H': 2.06, 'Br': 1.5, 'N': 7.16, 'Cl': 56.63}
			Density 2.43, Hardness None, Elements {'K': 1.05, 'Sn': 31.6, 'H': 2.06, 'Br': 1.5, 'N': 7.16, 'Cl': 56.63}
	Found duplicates of "Parachrysotile", with these properties :
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
			Density 2.59, Hardness 2.75, Elements {'Mg': 26.31, 'Si': 20.27, 'H': 1.45, 'O': 51.96}
	Found duplicates of "Paracostibite", with these properties :
			Density 6.9, Hardness 7.0, Elements {'Co': 27.7, 'Sb': 57.23, 'S': 15.07}
			Density 6.9, Hardness 7.0, Elements {'Co': 27.7, 'Sb': 57.23, 'S': 15.07}
	Found duplicates of "Parafransoletite", with these properties :
			Density 2.54, Hardness 2.5, Elements {'Ca': 22.81, 'Be': 3.42, 'P': 20.56, 'H': 1.63, 'O': 51.59}
			Density 2.54, Hardness 2.5, Elements {'Ca': 22.81, 'Be': 3.42, 'P': 20.56, 'H': 1.63, 'O': 51.59}
	Found duplicates of "Gearksutite", with these properties :
			Density 2.75, Hardness 2.0, Elements {'Ca': 22.51, 'Al': 15.15, 'H': 1.7, 'O': 17.97, 'F': 42.67}
			Density 2.75, Hardness 2.0, Elements {'Ca': 22.51, 'Al': 15.15, 'H': 1.7, 'O': 17.97, 'F': 42.67}
	Found duplicates of "Parageorgbokiite", with these properties :
			Density None, Hardness None, Elements {'Cu': 47.1, 'Se': 23.41, 'Cl': 10.51, 'O': 18.97}
			Density None, Hardness None, Elements {'Cu': 47.1, 'Se': 23.41, 'Cl': 10.51, 'O': 18.97}
	Found duplicates of "Rhodochrosite", with these properties :
			Density 3.69, Hardness 3.0, Elements {'Mn': 47.79, 'C': 10.45, 'O': 41.76}
			Density 3.69, Hardness 3.0, Elements {'Mn': 47.79, 'C': 10.45, 'O': 41.76}
			Density 3.69, Hardness 3.0, Elements {'Mn': 47.79, 'C': 10.45, 'O': 41.76}
	Found duplicates of "Parakuzmenkoite-Fe", with these properties :
			Density 3.0, Hardness 5.0, Elements {'K': 2.44, 'Ba': 8.02, 'Na': 0.36, 'Sr': 0.34, 'Ca': 0.16, 'Ti': 8.21, 'Mn': 1.5, 'Nb': 12.3, 'Zn': 0.25, 'Fe': 3.92, 'Si': 17.5, 'H': 1.19, 'O': 43.81}
			Density 3.0, Hardness 5.0, Elements {'K': 2.44, 'Ba': 8.02, 'Na': 0.36, 'Sr': 0.34, 'Ca': 0.16, 'Ti': 8.21, 'Mn': 1.5, 'Nb': 12.3, 'Zn': 0.25, 'Fe': 3.92, 'Si': 17.5, 'H': 1.19, 'O': 43.81}
	Found duplicates of "Paralaurionite", with these properties :
			Density 6.05, Hardness 3.0, Elements {'H': 0.39, 'Pb': 79.8, 'Cl': 13.65, 'O': 6.16}
			Density 6.05, Hardness 3.0, Elements {'H': 0.39, 'Pb': 79.8, 'Cl': 13.65, 'O': 6.16}
	Found duplicates of "Paranatisite", with these properties :
			Density 3.12, Hardness 5.0, Elements {'Na': 22.77, 'Ti': 23.71, 'Si': 13.91, 'O': 39.61}
			Density 3.12, Hardness 5.0, Elements {'Na': 22.77, 'Ti': 23.71, 'Si': 13.91, 'O': 39.61}
	Found duplicates of "Paranatrolite", with these properties :
			Density 2.21, Hardness 5.25, Elements {'K': 2.07, 'Na': 10.24, 'Ca': 0.58, 'Al': 16.5, 'Si': 18.66, 'H': 1.5, 'O': 50.45}
			Density 2.21, Hardness 5.25, Elements {'K': 2.07, 'Na': 10.24, 'Ca': 0.58, 'Al': 16.5, 'Si': 18.66, 'H': 1.5, 'O': 50.45}
	Found duplicates of "Paraniite-Y", with these properties :
			Density None, Hardness None, Elements {'Ca': 9.97, 'Y': 11.06, 'As': 9.32, 'W': 45.75, 'O': 23.89}
			Density None, Hardness None, Elements {'Ca': 9.97, 'Y': 11.06, 'As': 9.32, 'W': 45.75, 'O': 23.89}
	Found duplicates of "Pararealgar", with these properties :
			Density 3.52, Hardness 1.25, Elements {'As': 70.03, 'S': 29.97}
			Density 3.52, Hardness 1.25, Elements {'As': 70.03, 'S': 29.97}
	Found duplicates of "Pararsenolamprite", with these properties :
			Density 5.94, Hardness 2.25, Elements {'Sb': 7.96, 'As': 92.04}
			Density 5.94, Hardness 2.25, Elements {'Sb': 7.96, 'As': 92.04}
	Found duplicates of "Parascorodite", with these properties :
			Density 3.213, Hardness 1.5, Elements {'Fe': 24.2, 'As': 32.46, 'H': 1.75, 'O': 41.59}
			Density 3.213, Hardness 1.5, Elements {'Fe': 24.2, 'As': 32.46, 'H': 1.75, 'O': 41.59}
	Found duplicates of "Parasibirskite", with these properties :
			Density 2.5, Hardness 3.0, Elements {'Ca': 40.12, 'B': 10.82, 'H': 1.01, 'O': 48.05}
			Density 2.5, Hardness 3.0, Elements {'Ca': 40.12, 'B': 10.82, 'H': 1.01, 'O': 48.05}
	Found duplicates of "Paratooite-La", with these properties :
			Density None, Hardness 4.0, Elements {'Na': 2.54, 'Sr': 2.78, 'Ca': 5.6, 'La': 23.42, 'Pr': 6.89, 'Sm': 0.56, 'Gd': 0.73, 'Y': 0.58, 'Cu': 4.77, 'C': 9.06, 'Nd': 7.26, 'O': 35.56, 'F': 0.25}
			Density None, Hardness 4.0, Elements {'Na': 2.54, 'Sr': 2.78, 'Ca': 5.6, 'La': 23.42, 'Pr': 6.89, 'Sm': 0.56, 'Gd': 0.73, 'Y': 0.58, 'Cu': 4.77, 'C': 9.06, 'Nd': 7.26, 'O': 35.56, 'F': 0.25}
	Found duplicates of "Paratsepinite-Ba", with these properties :
			Density 2.88, Hardness 5.0, Elements {'K': 1.43, 'Ba': 10.06, 'Na': 1.35, 'Sr': 1.67, 'Ti': 10.75, 'Mn': 0.96, 'Nb': 8.13, 'Si': 18.33, 'H': 1.45, 'O': 45.87}
			Density 2.88, Hardness 5.0, Elements {'K': 1.43, 'Ba': 10.06, 'Na': 1.35, 'Sr': 1.67, 'Ti': 10.75, 'Mn': 0.96, 'Nb': 8.13, 'Si': 18.33, 'H': 1.45, 'O': 45.87}
	Found duplicates of "Paratsepinite-Na", with these properties :
			Density None, Hardness None, Elements {'K': 1.2, 'Na': 4.22, 'Sr': 8.05, 'Ca': 0.61, 'Ti': 12.56, 'Nb': 8.13, 'Si': 19.65, 'H': 0.79, 'O': 44.78}
			Density None, Hardness None, Elements {'K': 1.2, 'Na': 4.22, 'Sr': 8.05, 'Ca': 0.61, 'Ti': 12.56, 'Nb': 8.13, 'Si': 19.65, 'H': 0.79, 'O': 44.78}
	Found duplicates of "Paravinogradovite", with these properties :
			Density 2.77, Hardness 5.0, Elements {'K': 0.74, 'Na': 5.81, 'Mg': 0.08, 'Ti': 17.87, 'Be': 0.28, 'Al': 3.26, 'Fe': 2.91, 'Si': 20.5, 'H': 0.7, 'O': 47.86}
			Density 2.77, Hardness 5.0, Elements {'K': 0.74, 'Na': 5.81, 'Mg': 0.08, 'Ti': 17.87, 'Be': 0.28, 'Al': 3.26, 'Fe': 2.91, 'Si': 20.5, 'H': 0.7, 'O': 47.86}
	Found duplicates of "Potassicpargasite", with these properties :
			Density 3.25, Hardness 6.25, Elements {'K': 2.16, 'Na': 1.27, 'Ca': 8.84, 'Mg': 8.04, 'Fe': 12.32, 'Si': 24.78, 'H': 0.2, 'O': 41.99, 'F': 0.42}
			Density 3.25, Hardness 6.25, Elements {'K': 2.16, 'Na': 1.27, 'Ca': 8.84, 'Mg': 8.04, 'Fe': 12.32, 'Si': 24.78, 'H': 0.2, 'O': 41.99, 'F': 0.42}
			Density 3.25, Hardness 6.25, Elements {'K': 2.16, 'Na': 1.27, 'Ca': 8.84, 'Mg': 8.04, 'Fe': 12.32, 'Si': 24.78, 'H': 0.2, 'O': 41.99, 'F': 0.42}
	Found duplicates of "Parkinsonite", with these properties :
			Density 7.32, Hardness 2.25, Elements {'Mo': 2.83, 'Pb': 85.45, 'Cl': 4.18, 'O': 7.54}
			Density 7.32, Hardness 2.25, Elements {'Mo': 2.83, 'Pb': 85.45, 'Cl': 4.18, 'O': 7.54}
	Found duplicates of "Parvo-manganotremolite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 0.05, 'Na': 1.31, 'Ca': 5.38, 'Mg': 13.54, 'Mn': 6.79, 'Al': 1.73, 'Fe': 0.27, 'Si': 25.09, 'H': 0.24, 'O': 45.61}
			Density None, Hardness 6.0, Elements {'K': 0.05, 'Na': 1.31, 'Ca': 5.38, 'Mg': 13.54, 'Mn': 6.79, 'Al': 1.73, 'Fe': 0.27, 'Si': 25.09, 'H': 0.24, 'O': 45.61}
	Found duplicates of "Parvowinchite", with these properties :
			Density 3.07, Hardness 5.5, Elements {'Mg': 14.43, 'Mn': 13.05, 'Si': 26.68, 'H': 0.24, 'O': 45.6}
			Density 3.07, Hardness 5.5, Elements {'Mg': 14.43, 'Mn': 13.05, 'Si': 26.68, 'H': 0.24, 'O': 45.6}
	Found duplicates of "Parwanite", with these properties :
			Density None, Hardness None, Elements {'K': 0.55, 'Na': 0.97, 'Ca': 2.26, 'Mg': 4.11, 'Al': 12.16, 'P': 13.96, 'H': 3.8, 'O': 62.19}
			Density None, Hardness None, Elements {'K': 0.55, 'Na': 0.97, 'Ca': 2.26, 'Mg': 4.11, 'Al': 12.16, 'P': 13.96, 'H': 3.8, 'O': 62.19}
	Found duplicates of "Pasavaite", with these properties :
			Density 9.9, Hardness 2.0, Elements {'Te': 26.06, 'Pd': 31.84, 'Pb': 42.1}
			Density 9.9, Hardness 2.0, Elements {'Te': 26.06, 'Pd': 31.84, 'Pb': 42.1}
	Found duplicates of "Pattersonite", with these properties :
			Density 4.04, Hardness 4.5, Elements {'Fe': 25.25, 'P': 9.18, 'H': 1.06, 'Pb': 31.02, 'O': 33.49}
			Density 4.04, Hardness 4.5, Elements {'Fe': 25.25, 'P': 9.18, 'H': 1.06, 'Pb': 31.02, 'O': 33.49}
	Found duplicates of "Pauflerite", with these properties :
			Density 3.32, Hardness 3.5, Elements {'V': 30.97, 'S': 19.89, 'O': 49.13}
			Density 3.32, Hardness 3.5, Elements {'V': 30.97, 'S': 19.89, 'O': 49.13}
	Found duplicates of "Paulingite-K", with these properties :
			Density 2.16, Hardness 5.0, Elements {'K': 4.82, 'Ba': 0.69, 'Na': 0.61, 'Ca': 2.09, 'Al': 7.36, 'Si': 25.12, 'H': 2.46, 'O': 56.86}
			Density 2.16, Hardness 5.0, Elements {'K': 4.82, 'Ba': 0.69, 'Na': 0.61, 'Ca': 2.09, 'Al': 7.36, 'Si': 25.12, 'H': 2.46, 'O': 56.86}
	Found duplicates of "Pautovite", with these properties :
			Density None, Hardness 2.5, Elements {'Cs': 35.92, 'K': 0.23, 'Rb': 1.27, 'Tl': 0.61, 'Fe': 33.5, 'S': 28.47}
			Density None, Hardness 2.5, Elements {'Cs': 35.92, 'K': 0.23, 'Rb': 1.27, 'Tl': 0.61, 'Fe': 33.5, 'S': 28.47}
	Found duplicates of "Arsenpolybasite", with these properties :
			Density 6.2, Hardness 3.0, Elements {'Cu': 12.25, 'Ag': 62.39, 'Sb': 2.93, 'As': 5.42, 'S': 17.0}
			Density 6.2, Hardness 3.0, Elements {'Cu': 12.25, 'Ag': 62.39, 'Sb': 2.93, 'As': 5.42, 'S': 17.0}
			Density 6.2, Hardness 3.0, Elements {'Cu': 12.25, 'Ag': 62.39, 'Sb': 2.93, 'As': 5.42, 'S': 17.0}
	Found duplicates of "Pearceite", with these properties :
			Density 6.15, Hardness 3.0, Elements {'Cu': 11.64, 'Ag': 62.5, 'Sb': 4.82, 'As': 4.22, 'S': 16.82}
			Density 6.15, Hardness 3.0, Elements {'Cu': 11.64, 'Ag': 62.5, 'Sb': 4.82, 'As': 4.22, 'S': 16.82}
	Found duplicates of "Pectolite", with these properties :
			Density 2.86, Hardness 5.0, Elements {'Na': 6.92, 'Ca': 24.11, 'Si': 25.35, 'H': 0.3, 'O': 43.32}
			Density 2.86, Hardness 5.0, Elements {'Na': 6.92, 'Ca': 24.11, 'Si': 25.35, 'H': 0.3, 'O': 43.32}
	Found duplicates of "Ferrotaaffeite-6N3S", with these properties :
			Density 4.07, Hardness 8.25, Elements {'Mg': 1.03, 'Be': 1.72, 'Al': 34.43, 'Zn': 4.17, 'Fe': 17.81, 'O': 40.83}
			Density 4.07, Hardness 8.25, Elements {'Mg': 1.03, 'Be': 1.72, 'Al': 34.43, 'Zn': 4.17, 'Fe': 17.81, 'O': 40.83}
	Found duplicates of "Pekovite", with these properties :
			Density 3.35, Hardness 7.0, Elements {'Sr': 29.21, 'Si': 19.49, 'B': 7.32, 'O': 43.98}
			Density 3.35, Hardness 7.0, Elements {'Sr': 29.21, 'Si': 19.49, 'B': 7.32, 'O': 43.98}
			Density 3.35, Hardness 7.0, Elements {'Sr': 29.21, 'Si': 19.49, 'B': 7.32, 'O': 43.98}
	Found duplicates of "Pellouxite", with these properties :
			Density None, Hardness None, Elements {'Cu': 0.9, 'Ag': 0.6, 'Sb': 31.27, 'Pb': 47.36, 'S': 19.15, 'Cl': 0.33, 'O': 0.39}
			Density None, Hardness None, Elements {'Cu': 0.9, 'Ag': 0.6, 'Sb': 31.27, 'Pb': 47.36, 'S': 19.15, 'Cl': 0.33, 'O': 0.39}
	Found duplicates of "Pellyite", with these properties :
			Density 3.51, Hardness 6.0, Elements {'Ba': 32.63, 'Ca': 4.76, 'Mg': 2.31, 'Fe': 7.96, 'Si': 20.02, 'O': 32.31}
			Density 3.51, Hardness 6.0, Elements {'Ba': 32.63, 'Ca': 4.76, 'Mg': 2.31, 'Fe': 7.96, 'Si': 20.02, 'O': 32.31}
	Found duplicates of "Karpatite", with these properties :
			Density 1.35, Hardness 1.5, Elements {'H': 4.03, 'C': 95.97}
			Density 1.35, Hardness 1.5, Elements {'H': 4.03, 'C': 95.97}
			Density 1.35, Hardness 1.5, Elements {'H': 4.03, 'C': 95.97}
			Density 1.35, Hardness 1.5, Elements {'H': 4.03, 'C': 95.97}
	Found duplicates of "Magnesionigerite-2N1S", with these properties :
			Density 4.22, Hardness 8.25, Elements {'Mg': 4.79, 'Al': 29.95, 'Zn': 4.07, 'Fe': 5.79, 'Si': 0.58, 'Sn': 14.78, 'H': 0.21, 'O': 39.83}
			Density 4.22, Hardness 8.25, Elements {'Mg': 4.79, 'Al': 29.95, 'Zn': 4.07, 'Fe': 5.79, 'Si': 0.58, 'Sn': 14.78, 'H': 0.21, 'O': 39.83}
			Density 4.22, Hardness 8.25, Elements {'Mg': 4.79, 'Al': 29.95, 'Zn': 4.07, 'Fe': 5.79, 'Si': 0.58, 'Sn': 14.78, 'H': 0.21, 'O': 39.83}
	Found duplicates of "Magnesionigerite-6N6S", with these properties :
			Density 4.22, Hardness 8.25, Elements {'Mg': 4.79, 'Al': 29.95, 'Zn': 4.07, 'Fe': 5.79, 'Si': 0.58, 'Sn': 14.78, 'H': 0.21, 'O': 39.83}
			Density 4.22, Hardness 8.25, Elements {'Mg': 4.79, 'Al': 29.95, 'Zn': 4.07, 'Fe': 5.79, 'Si': 0.58, 'Sn': 14.78, 'H': 0.21, 'O': 39.83}
	Found duplicates of "Penikisite", with these properties :
			Density 3.79, Hardness 4.0, Elements {'Ba': 23.53, 'Mg': 7.29, 'Al': 9.24, 'Fe': 2.39, 'P': 15.92, 'H': 0.52, 'O': 41.11}
			Density 3.79, Hardness 4.0, Elements {'Ba': 23.53, 'Mg': 7.29, 'Al': 9.24, 'Fe': 2.39, 'P': 15.92, 'H': 0.52, 'O': 41.11}
	Found duplicates of "Pennantite", with these properties :
			Density 3.06, Hardness 2.5, Elements {'Mn': 38.75, 'Al': 7.61, 'Si': 11.88, 'H': 1.14, 'O': 40.62}
			Density 3.06, Hardness 2.5, Elements {'Mn': 38.75, 'Al': 7.61, 'Si': 11.88, 'H': 1.14, 'O': 40.62}
	Found duplicates of "Clinochlore", with these properties :
			Density 2.65, Hardness 2.25, Elements {'Mg': 15.31, 'Al': 9.07, 'Fe': 11.73, 'Si': 14.16, 'H': 1.35, 'O': 48.38}
			Density 2.65, Hardness 2.25, Elements {'Mg': 15.31, 'Al': 9.07, 'Fe': 11.73, 'Si': 14.16, 'H': 1.35, 'O': 48.38}
			Density 2.65, Hardness 2.25, Elements {'Mg': 15.31, 'Al': 9.07, 'Fe': 11.73, 'Si': 14.16, 'H': 1.35, 'O': 48.38}
			Density 2.65, Hardness 2.25, Elements {'Mg': 15.31, 'Al': 9.07, 'Fe': 11.73, 'Si': 14.16, 'H': 1.35, 'O': 48.38}
			Density 2.65, Hardness 2.25, Elements {'Mg': 15.31, 'Al': 9.07, 'Fe': 11.73, 'Si': 14.16, 'H': 1.35, 'O': 48.38}
			Density 2.65, Hardness 2.25, Elements {'Mg': 15.31, 'Al': 9.07, 'Fe': 11.73, 'Si': 14.16, 'H': 1.35, 'O': 48.38}
			Density 2.65, Hardness 2.25, Elements {'Mg': 15.31, 'Al': 9.07, 'Fe': 11.73, 'Si': 14.16, 'H': 1.35, 'O': 48.38}
	Found duplicates of "Penobsquisite", with these properties :
			Density 2.26, Hardness 3.0, Elements {'Ca': 12.32, 'Fe': 8.58, 'B': 14.95, 'H': 2.17, 'Cl': 5.45, 'O': 56.54}
			Density 2.26, Hardness 3.0, Elements {'Ca': 12.32, 'Fe': 8.58, 'B': 14.95, 'H': 2.17, 'Cl': 5.45, 'O': 56.54}
	Found duplicates of "Penroseite", with these properties :
			Density 6.66, Hardness 2.75, Elements {'Co': 8.14, 'Cu': 2.93, 'Ni': 16.22, 'Se': 72.72}
			Density 6.66, Hardness 2.75, Elements {'Co': 8.14, 'Cu': 2.93, 'Ni': 16.22, 'Se': 72.72}
	Found duplicates of "Pentlandite", with these properties :
			Density 4.8, Hardness 3.75, Elements {'Fe': 32.56, 'Ni': 34.21, 'S': 33.23}
			Density 4.8, Hardness 3.75, Elements {'Fe': 32.56, 'Ni': 34.21, 'S': 33.23}
	Found duplicates of "Peprossiite-Ce", with these properties :
			Density 3.45, Hardness 2.0, Elements {'La': 8.52, 'Ce': 25.78, 'Al': 13.24, 'B': 10.61, 'O': 41.86}
			Density 3.45, Hardness 2.0, Elements {'La': 8.52, 'Ce': 25.78, 'Al': 13.24, 'B': 10.61, 'O': 41.86}
			Density 3.45, Hardness 2.0, Elements {'La': 8.52, 'Ce': 25.78, 'Al': 13.24, 'B': 10.61, 'O': 41.86}
	Found duplicates of "Percleveite-Ce", with these properties :
			Density None, Hardness 6.0, Elements {'La': 12.86, 'Ce': 27.53, 'Pr': 2.86, 'Sm': 2.38, 'Gd': 2.13, 'Dy': 0.37, 'Y': 2.41, 'Si': 12.75, 'Nd': 11.4, 'O': 25.3}
			Density None, Hardness 6.0, Elements {'La': 12.86, 'Ce': 27.53, 'Pr': 2.86, 'Sm': 2.38, 'Gd': 2.13, 'Dy': 0.37, 'Y': 2.41, 'Si': 12.75, 'Nd': 11.4, 'O': 25.3}
	Found duplicates of "Pseudoboleite", with these properties :
			Density 5.0, Hardness 2.5, Elements {'Cu': 13.91, 'H': 0.44, 'Pb': 58.59, 'Cl': 20.05, 'O': 7.01}
			Density 5.0, Hardness 2.5, Elements {'Cu': 13.91, 'H': 0.44, 'Pb': 58.59, 'Cl': 20.05, 'O': 7.01}
	Found duplicates of "Perovskite", with these properties :
			Density 4.0, Hardness 5.5, Elements {'Ca': 29.48, 'Ti': 35.22, 'O': 35.3}
			Density 4.0, Hardness 5.5, Elements {'Ca': 29.48, 'Ti': 35.22, 'O': 35.3}
			Density 4.0, Hardness 5.5, Elements {'Ca': 29.48, 'Ti': 35.22, 'O': 35.3}
			Density 4.0, Hardness 5.5, Elements {'Ca': 29.48, 'Ti': 35.22, 'O': 35.3}
	Found duplicates of "Perraultite", with these properties :
			Density 3.71, Hardness 4.0, Elements {'K': 1.11, 'Ba': 11.66, 'Na': 1.95, 'Ca': 1.13, 'Ti': 10.3, 'Mn': 15.55, 'Nb': 1.05, 'Fe': 9.49, 'Si': 12.72, 'H': 0.26, 'O': 32.61, 'F': 2.15}
			Density 3.71, Hardness 4.0, Elements {'K': 1.11, 'Ba': 11.66, 'Na': 1.95, 'Ca': 1.13, 'Ti': 10.3, 'Mn': 15.55, 'Nb': 1.05, 'Fe': 9.49, 'Si': 12.72, 'H': 0.26, 'O': 32.61, 'F': 2.15}
	Found duplicates of "Qusongite", with these properties :
			Density None, Hardness 9.5, Elements {'Cr': 0.53, 'W': 93.45, 'C': 6.02}
			Density None, Hardness 9.5, Elements {'Cr': 0.53, 'W': 93.45, 'C': 6.02}
			Density None, Hardness 9.5, Elements {'Cr': 0.53, 'W': 93.45, 'C': 6.02}
	Found duplicates of "Yarlongite", with these properties :
			Density None, Hardness 5.75, Elements {'Cr': 38.65, 'Fe': 41.51, 'Ni': 10.91, 'C': 8.93}
			Density None, Hardness 5.75, Elements {'Cr': 38.65, 'Fe': 41.51, 'Ni': 10.91, 'C': 8.93}
	Found duplicates of "Horomanite", with these properties :
			Density None, Hardness None, Elements {'Fe': 43.65, 'Ni': 22.94, 'S': 33.42}
			Density None, Hardness None, Elements {'Fe': 43.65, 'Ni': 22.94, 'S': 33.42}
	Found duplicates of "Samaniite", with these properties :
			Density None, Hardness None, Elements {'Fe': 35.79, 'Cu': 16.29, 'Ni': 15.04, 'S': 32.88}
			Density None, Hardness None, Elements {'Fe': 35.79, 'Cu': 16.29, 'Ni': 15.04, 'S': 32.88}
	Found duplicates of "Bussyite-Ce", with these properties :
			Density 3.0, Hardness None, Elements {'K': 0.04, 'Na': 5.88, 'Ca': 3.98, 'Eu': 0.09, 'La': 2.35, 'Ce': 8.68, 'Pr': 1.09, 'Sm': 0.89, 'Gd': 0.92, 'Y': 1.62, 'Th': 3.02, 'Mg': 0.02, 'Mn': 2.0, 'Be': 3.12, 'Al': 0.45, 'Si': 18.8, 'H': 0.25, 'Nd': 4.05, 'O': 38.91, 'F': 3.82}
			Density 3.0, Hardness None, Elements {'K': 0.04, 'Na': 5.88, 'Ca': 3.98, 'Eu': 0.09, 'La': 2.35, 'Ce': 8.68, 'Pr': 1.09, 'Sm': 0.89, 'Gd': 0.92, 'Y': 1.62, 'Th': 3.02, 'Mg': 0.02, 'Mn': 2.0, 'Be': 3.12, 'Al': 0.45, 'Si': 18.8, 'H': 0.25, 'Nd': 4.05, 'O': 38.91, 'F': 3.82}
	Found duplicates of "Suhailite", with these properties :
			Density None, Hardness 2.5, Elements {'K': 3.07, 'Na': 0.36, 'Ca': 0.36, 'Mg': 3.87, 'Ti': 2.36, 'Mn': 0.12, 'Al': 10.6, 'Fe': 16.67, 'Si': 16.83, 'H': 0.95, 'N': 1.73, 'O': 43.08}
			Density None, Hardness 2.5, Elements {'K': 3.07, 'Na': 0.36, 'Ca': 0.36, 'Mg': 3.87, 'Ti': 2.36, 'Mn': 0.12, 'Al': 10.6, 'Fe': 16.67, 'Si': 16.83, 'H': 0.95, 'N': 1.73, 'O': 43.08}
	Found duplicates of "Ivanyukite-Na", with these properties :
			Density None, Hardness None, Elements {'Na': 6.68, 'Ti': 27.84, 'Si': 12.25, 'H': 2.05, 'O': 51.17}
			Density None, Hardness None, Elements {'Na': 6.68, 'Ti': 27.84, 'Si': 12.25, 'H': 2.05, 'O': 51.17}
	Found duplicates of "Ivanyukite-K", with these properties :
			Density None, Hardness None, Elements {'K': 10.1, 'Ti': 24.74, 'Si': 10.88, 'H': 2.6, 'O': 51.67}
			Density None, Hardness None, Elements {'K': 10.1, 'Ti': 24.74, 'Si': 10.88, 'H': 2.6, 'O': 51.67}
	Found duplicates of "Ivanyukite-Cu", with these properties :
			Density None, Hardness None, Elements {'Ti': 26.47, 'Cu': 8.78, 'Si': 11.65, 'H': 2.23, 'O': 50.87}
			Density None, Hardness None, Elements {'Ti': 26.47, 'Cu': 8.78, 'Si': 11.65, 'H': 2.23, 'O': 50.87}
	Found duplicates of "Biachellaite", with these properties :
			Density None, Hardness None, Elements {'K': 0.83, 'Na': 9.32, 'Ca': 10.26, 'Al': 13.81, 'Si': 14.38, 'H': 0.21, 'S': 5.47, 'O': 45.72}
			Density None, Hardness None, Elements {'K': 0.83, 'Na': 9.32, 'Ca': 10.26, 'Al': 13.81, 'Si': 14.38, 'H': 0.21, 'S': 5.47, 'O': 45.72}
	Found duplicates of "Colimaite", with these properties :
			Density None, Hardness None, Elements {'K': 38.9, 'Na': 0.47, 'V': 17.7, 'S': 42.94}
			Density None, Hardness None, Elements {'K': 38.9, 'Na': 0.47, 'V': 17.7, 'S': 42.94}
	Found duplicates of "Cupropearceite", with these properties :
			Density None, Hardness None, Elements {'Cu': 23.19, 'Ag': 50.61, 'As': 7.81, 'S': 18.39}
			Density None, Hardness None, Elements {'Cu': 23.19, 'Ag': 50.61, 'As': 7.81, 'S': 18.39}
	Found duplicates of "Kumdykolite", with these properties :
			Density None, Hardness None, Elements {'Na': 8.77, 'Al': 10.29, 'Si': 32.13, 'O': 48.81}
			Density None, Hardness None, Elements {'Na': 8.77, 'Al': 10.29, 'Si': 32.13, 'O': 48.81}
	Found duplicates of "Alfredstelznerite", with these properties :
			Density None, Hardness None, Elements {'Ca': 11.97, 'B': 12.91, 'H': 4.66, 'O': 70.46}
			Density None, Hardness None, Elements {'Ca': 11.97, 'B': 12.91, 'H': 4.66, 'O': 70.46}
	Found duplicates of "Eldfellite", with these properties :
			Density None, Hardness None, Elements {'Na': 8.48, 'Fe': 20.61, 'S': 23.67, 'O': 47.24}
			Density None, Hardness None, Elements {'Na': 8.48, 'Fe': 20.61, 'S': 23.67, 'O': 47.24}
	Found duplicates of "Voloshinite", with these properties :
			Density None, Hardness None, Elements {'Rb': 19.31, 'Li': 1.57, 'Al': 12.19, 'Si': 22.21, 'O': 36.14, 'F': 8.58}
			Density None, Hardness None, Elements {'Rb': 19.31, 'Li': 1.57, 'Al': 12.19, 'Si': 22.21, 'O': 36.14, 'F': 8.58}
	Found duplicates of "Potassic-ferropargasite", with these properties :
			Density None, Hardness None, Elements {'K': 4.0, 'Ca': 8.2, 'Al': 8.28, 'Fe': 22.84, 'Si': 17.23, 'H': 0.21, 'O': 39.26}
			Density None, Hardness None, Elements {'K': 4.0, 'Ca': 8.2, 'Al': 8.28, 'Fe': 22.84, 'Si': 17.23, 'H': 0.21, 'O': 39.26}
	Found duplicates of "Klochite", with these properties :
			Density None, Hardness None, Elements {'K': 3.29, 'Na': 1.94, 'Zn': 16.53, 'Fe': 9.41, 'Si': 28.39, 'O': 40.44}
			Density None, Hardness None, Elements {'K': 3.29, 'Na': 1.94, 'Zn': 16.53, 'Fe': 9.41, 'Si': 28.39, 'O': 40.44}
	Found duplicates of "Burgessite", with these properties :
			Density 2.93, Hardness 3.0, Elements {'Ca': 0.16, 'Co': 21.16, 'Ni': 2.77, 'As': 30.75, 'H': 2.48, 'O': 42.68}
			Density 2.93, Hardness 3.0, Elements {'Ca': 0.16, 'Co': 21.16, 'Ni': 2.77, 'As': 30.75, 'H': 2.48, 'O': 42.68}
	Found duplicates of "Xieite", with these properties :
			Density None, Hardness None, Elements {'Mg': 1.49, 'Ti': 1.81, 'Mn': 0.26, 'Al': 3.18, 'V': 0.48, 'Cr': 39.71, 'Fe': 22.91, 'O': 30.17}
			Density None, Hardness None, Elements {'Mg': 1.49, 'Ti': 1.81, 'Mn': 0.26, 'Al': 3.18, 'V': 0.48, 'Cr': 39.71, 'Fe': 22.91, 'O': 30.17}
	Found duplicates of "Kunatite", with these properties :
			Density None, Hardness None, Elements {'Fe': 24.59, 'Cu': 13.99, 'P': 13.64, 'H': 2.0, 'O': 45.79}
			Density None, Hardness None, Elements {'Fe': 24.59, 'Cu': 13.99, 'P': 13.64, 'H': 2.0, 'O': 45.79}
	Found duplicates of "Surkhobite", with these properties :
			Density 3.84, Hardness 4.5, Elements {'K': 1.56, 'Ba': 10.06, 'Na': 1.7, 'Sr': 0.22, 'Ca': 1.82, 'Mg': 0.08, 'Zr': 0.26, 'Ti': 9.74, 'Mn': 12.74, 'Nb': 1.5, 'Al': 0.01, 'Fe': 12.4, 'Si': 12.8, 'H': 0.13, 'O': 32.02, 'F': 2.96}
			Density 3.84, Hardness 4.5, Elements {'K': 1.56, 'Ba': 10.06, 'Na': 1.7, 'Sr': 0.22, 'Ca': 1.82, 'Mg': 0.08, 'Zr': 0.26, 'Ti': 9.74, 'Mn': 12.74, 'Nb': 1.5, 'Al': 0.01, 'Fe': 12.4, 'Si': 12.8, 'H': 0.13, 'O': 32.02, 'F': 2.96}
			Density 3.84, Hardness 4.5, Elements {'K': 1.56, 'Ba': 10.06, 'Na': 1.7, 'Sr': 0.22, 'Ca': 1.82, 'Mg': 0.08, 'Zr': 0.26, 'Ti': 9.74, 'Mn': 12.74, 'Nb': 1.5, 'Al': 0.01, 'Fe': 12.4, 'Si': 12.8, 'H': 0.13, 'O': 32.02, 'F': 2.96}
			Density 3.84, Hardness 4.5, Elements {'K': 1.56, 'Ba': 10.06, 'Na': 1.7, 'Sr': 0.22, 'Ca': 1.82, 'Mg': 0.08, 'Zr': 0.26, 'Ti': 9.74, 'Mn': 12.74, 'Nb': 1.5, 'Al': 0.01, 'Fe': 12.4, 'Si': 12.8, 'H': 0.13, 'O': 32.02, 'F': 2.96}
	Found duplicates of "Aluminocerite-Ce", with these properties :
			Density None, Hardness 5.0, Elements {'Ca': 6.04, 'La': 6.44, 'Ce': 20.22, 'Pr': 3.1, 'Sm': 3.83, 'Gd': 2.73, 'Dy': 0.38, 'Y': 1.34, 'Yb': 0.1, 'Al': 1.33, 'Fe': 0.32, 'Si': 11.4, 'H': 0.41, 'Nd': 13.54, 'O': 28.8}
			Density None, Hardness 5.0, Elements {'Ca': 6.04, 'La': 6.44, 'Ce': 20.22, 'Pr': 3.1, 'Sm': 3.83, 'Gd': 2.73, 'Dy': 0.38, 'Y': 1.34, 'Yb': 0.1, 'Al': 1.33, 'Fe': 0.32, 'Si': 11.4, 'H': 0.41, 'Nd': 13.54, 'O': 28.8}
	Found duplicates of "Hazenite", with these properties :
			Density 1.91, Hardness 2.25, Elements {'K': 7.07, 'Na': 4.16, 'Mg': 8.79, 'P': 11.21, 'H': 5.1, 'O': 63.67}
			Density 1.91, Hardness 2.25, Elements {'K': 7.07, 'Na': 4.16, 'Mg': 8.79, 'P': 11.21, 'H': 5.1, 'O': 63.67}
	Found duplicates of "Burovaite-Ca", with these properties :
			Density None, Hardness None, Elements {'K': 1.87, 'Ba': 0.25, 'Na': 3.19, 'Sr': 0.16, 'Ca': 2.96, 'Ta': 0.16, 'Ti': 10.65, 'Nb': 10.64, 'Al': 0.08, 'Zn': 0.03, 'Fe': 0.2, 'Si': 20.02, 'H': 1.38, 'O': 48.41}
			Density None, Hardness None, Elements {'K': 1.87, 'Ba': 0.25, 'Na': 3.19, 'Sr': 0.16, 'Ca': 2.96, 'Ta': 0.16, 'Ti': 10.65, 'Nb': 10.64, 'Al': 0.08, 'Zn': 0.03, 'Fe': 0.2, 'Si': 20.02, 'H': 1.38, 'O': 48.41}
	Found duplicates of "Droninoite", with these properties :
			Density None, Hardness 1.25, Elements {'Fe': 21.73, 'Ni': 28.68, 'H': 2.66, 'Cl': 12.99, 'O': 33.95}
			Density None, Hardness 1.25, Elements {'Fe': 21.73, 'Ni': 28.68, 'H': 2.66, 'Cl': 12.99, 'O': 33.95}
	Found duplicates of "Cupropolybasite", with these properties :
			Density None, Hardness None, Elements {'Cu': 22.11, 'Ag': 48.25, 'Sb': 12.1, 'S': 17.53}
			Density None, Hardness None, Elements {'Cu': 22.11, 'Ag': 48.25, 'Sb': 12.1, 'S': 17.53}
	Found duplicates of "Proshchenkoite-Y", with these properties :
			Density 4.72, Hardness 5.0, Elements {'Na': 0.98, 'Ca': 3.77, 'RE': 40.04, 'Y': 12.13, 'Th': 0.86, 'Ti': 0.04, 'Mn': 1.86, 'Fe': 1.71, 'Si': 6.54, 'B': 1.28, 'As': 0.11, 'P': 0.8, 'Pb': 0.08, 'O': 20.39, 'F': 9.42}
			Density 4.72, Hardness 5.0, Elements {'Na': 0.98, 'Ca': 3.77, 'RE': 40.04, 'Y': 12.13, 'Th': 0.86, 'Ti': 0.04, 'Mn': 1.86, 'Fe': 1.71, 'Si': 6.54, 'B': 1.28, 'As': 0.11, 'P': 0.8, 'Pb': 0.08, 'O': 20.39, 'F': 9.42}
	Found duplicates of "Angastonite", with these properties :
			Density 2.47, Hardness 2.0, Elements {'Ca': 7.98, 'Mg': 4.84, 'Al': 10.74, 'P': 12.33, 'H': 3.61, 'O': 60.5}
			Density 2.47, Hardness 2.0, Elements {'Ca': 7.98, 'Mg': 4.84, 'Al': 10.74, 'P': 12.33, 'H': 3.61, 'O': 60.5}
	Found duplicates of "Brownleeite", with these properties :
			Density None, Hardness None, Elements {'Mn': 66.17, 'Si': 33.83}
			Density None, Hardness None, Elements {'Mn': 66.17, 'Si': 33.83}
	Found duplicates of "Tazieffite", with these properties :
			Density None, Hardness None, Elements {'Cd': 2.68, 'Bi': 7.48, 'As': 16.99, 'Pb': 49.47, 'S': 19.14, 'Cl': 4.23}
			Density None, Hardness None, Elements {'Cd': 2.68, 'Bi': 7.48, 'As': 16.99, 'Pb': 49.47, 'S': 19.14, 'Cl': 4.23}
	Found duplicates of "Plimerite", with these properties :
			Density None, Hardness None, Elements {'Zn': 9.93, 'Fe': 33.91, 'P': 14.11, 'H': 0.77, 'O': 41.29}
			Density None, Hardness None, Elements {'Zn': 9.93, 'Fe': 33.91, 'P': 14.11, 'H': 0.77, 'O': 41.29}
	Found duplicates of "Steropesite", with these properties :
			Density None, Hardness None, Elements {'Na': 0.02, 'Tl': 54.84, 'Bi': 23.48, 'Br': 4.15, 'Cl': 17.51}
			Density None, Hardness None, Elements {'Na': 0.02, 'Tl': 54.84, 'Bi': 23.48, 'Br': 4.15, 'Cl': 17.51}
	Found duplicates of "Aiolosite", with these properties :
			Density None, Hardness None, Elements {'Na': 14.72, 'Bi': 33.46, 'S': 15.4, 'Cl': 5.68, 'O': 30.74}
			Density None, Hardness None, Elements {'Na': 14.72, 'Bi': 33.46, 'S': 15.4, 'Cl': 5.68, 'O': 30.74}
	Found duplicates of "Tistarite", with these properties :
			Density None, Hardness 8.5, Elements {'Mg': 1.19, 'Zr': 0.64, 'Ti': 63.77, 'Al': 0.76, 'O': 33.64}
			Density None, Hardness 8.5, Elements {'Mg': 1.19, 'Zr': 0.64, 'Ti': 63.77, 'Al': 0.76, 'O': 33.64}
	Found duplicates of "Kamarizaite", with these properties :
			Density None, Hardness None, Elements {'Fe': 30.44, 'As': 27.22, 'H': 1.65, 'O': 40.69}
			Density None, Hardness None, Elements {'Fe': 30.44, 'As': 27.22, 'H': 1.65, 'O': 40.69}
	Found duplicates of "Punkaruaivite", with these properties :
			Density None, Hardness None, Elements {'Li': 1.51, 'Ti': 20.81, 'Si': 24.42, 'H': 1.1, 'O': 52.16}
			Density None, Hardness None, Elements {'Li': 1.51, 'Ti': 20.81, 'Si': 24.42, 'H': 1.1, 'O': 52.16}
	Found duplicates of "Friedrichbeckeite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 3.46, 'Na': 2.01, 'Mg': 6.78, 'Mn': 1.57, 'Be': 1.68, 'Fe': 1.36, 'Si': 34.3, 'O': 48.84}
			Density None, Hardness 6.0, Elements {'K': 3.46, 'Na': 2.01, 'Mg': 6.78, 'Mn': 1.57, 'Be': 1.68, 'Fe': 1.36, 'Si': 34.3, 'O': 48.84}
	Found duplicates of "Demicheleite-Cl", with these properties :
			Density None, Hardness None, Elements {'Bi': 75.58, 'S': 11.6, 'Cl': 12.82}
			Density None, Hardness None, Elements {'Bi': 75.58, 'S': 11.6, 'Cl': 12.82}
	Found duplicates of "Steverustite", with these properties :
			Density None, Hardness None, Elements {'Cu': 4.1, 'H': 0.54, 'Pb': 66.79, 'S': 12.4, 'O': 16.16}
			Density None, Hardness None, Elements {'Cu': 4.1, 'H': 0.54, 'Pb': 66.79, 'S': 12.4, 'O': 16.16}
	Found duplicates of "Alflarsenite", with these properties :
			Density None, Hardness None, Elements {'Na': 4.57, 'Ca': 15.92, 'Be': 5.37, 'Si': 22.31, 'H': 1.0, 'O': 50.84}
			Density None, Hardness None, Elements {'Na': 4.57, 'Ca': 15.92, 'Be': 5.37, 'Si': 22.31, 'H': 1.0, 'O': 50.84}
	Found duplicates of "Plumbophyllite", with these properties :
			Density None, Hardness None, Elements {'Si': 15.94, 'H': 0.29, 'Pb': 58.8, 'O': 24.97}
			Density None, Hardness None, Elements {'Si': 15.94, 'H': 0.29, 'Pb': 58.8, 'O': 24.97}
	Found duplicates of "Brumadoite", with these properties :
			Density None, Hardness None, Elements {'Ca': 0.07, 'Cu': 34.44, 'Si': 0.26, 'Te': 22.17, 'H': 2.64, 'Pb': 1.55, 'O': 38.87}
			Density None, Hardness None, Elements {'Ca': 0.07, 'Cu': 34.44, 'Si': 0.26, 'Te': 22.17, 'H': 2.64, 'Pb': 1.55, 'O': 38.87}
	Found duplicates of "Davisite", with these properties :
			Density None, Hardness None, Elements {'Ca': 16.83, 'Y': 0.38, 'Mg': 1.65, 'Zr': 1.55, 'Sc': 9.54, 'Ti': 5.28, 'Al': 11.1, 'V': 0.43, 'Fe': 0.24, 'Si': 12.27, 'O': 40.73}
			Density None, Hardness None, Elements {'Ca': 16.83, 'Y': 0.38, 'Mg': 1.65, 'Zr': 1.55, 'Sc': 9.54, 'Ti': 5.28, 'Al': 11.1, 'V': 0.43, 'Fe': 0.24, 'Si': 12.27, 'O': 40.73}
	Found duplicates of "Wakefieldite-Nd", with these properties :
			Density None, Hardness None, Elements {'V': 19.65, 'Nd': 55.65, 'O': 24.69}
			Density None, Hardness None, Elements {'V': 19.65, 'Nd': 55.65, 'O': 24.69}
	Found duplicates of "Yegorovite", with these properties :
			Density None, Hardness None, Elements {'Na': 17.47, 'Si': 21.34, 'H': 3.45, 'O': 57.75}
			Density None, Hardness None, Elements {'Na': 17.47, 'Si': 21.34, 'H': 3.45, 'O': 57.75}
	Found duplicates of "Joelbruggerite", with these properties :
			Density None, Hardness None, Elements {'Zn': 14.93, 'Sb': 9.26, 'As': 11.4, 'H': 0.08, 'Pb': 47.29, 'O': 17.04}
			Density None, Hardness None, Elements {'Zn': 14.93, 'Sb': 9.26, 'As': 11.4, 'H': 0.08, 'Pb': 47.29, 'O': 17.04}
	Found duplicates of "Florkeite", with these properties :
			Density None, Hardness None, Elements {'K': 8.44, 'Na': 1.65, 'Ca': 5.77, 'Al': 15.54, 'Si': 16.17, 'H': 1.74, 'O': 50.68}
			Density None, Hardness None, Elements {'K': 8.44, 'Na': 1.65, 'Ca': 5.77, 'Al': 15.54, 'Si': 16.17, 'H': 1.74, 'O': 50.68}
	Found duplicates of "Bobdownsite", with these properties :
			Density None, Hardness None, Elements {'Na': 0.52, 'Ca': 33.3, 'Mg': 1.66, 'Al': 0.28, 'Fe': 0.9, 'P': 20.56, 'O': 40.97, 'F': 1.8}
			Density None, Hardness None, Elements {'Na': 0.52, 'Ca': 33.3, 'Mg': 1.66, 'Al': 0.28, 'Fe': 0.9, 'P': 20.56, 'O': 40.97, 'F': 1.8}
	Found duplicates of "Chegemite", with these properties :
			Density None, Hardness None, Elements {'Ca': 47.48, 'Si': 14.26, 'H': 0.34, 'O': 37.91}
			Density None, Hardness None, Elements {'Ca': 47.48, 'Si': 14.26, 'H': 0.34, 'O': 37.91}
	Found duplicates of "Kyanoxalite", with these properties :
			Density None, Hardness None, Elements {'Na': 15.59, 'Al': 14.38, 'Si': 17.69, 'H': 0.98, 'C': 1.75, 'O': 49.61}
			Density None, Hardness None, Elements {'Na': 15.59, 'Al': 14.38, 'Si': 17.69, 'H': 0.98, 'C': 1.75, 'O': 49.61}
	Found duplicates of "Grossmanite", with these properties :
			Density None, Hardness None, Elements {'Ca': 16.77, 'Ti': 20.03, 'Al': 11.29, 'Si': 11.75, 'O': 40.16}
			Density None, Hardness None, Elements {'Ca': 16.77, 'Ti': 20.03, 'Al': 11.29, 'Si': 11.75, 'O': 40.16}
	Found duplicates of "Kumtyubeite", with these properties :
			Density None, Hardness None, Elements {'Ca': 47.42, 'Si': 13.29, 'O': 30.29, 'F': 8.99}
			Density None, Hardness None, Elements {'Ca': 47.42, 'Si': 13.29, 'O': 30.29, 'F': 8.99}
	Found duplicates of "Alumoakermanite", with these properties :
			Density None, Hardness 4.5, Elements {'Na': 4.28, 'Ca': 22.36, 'Mg': 2.71, 'Al': 6.02, 'Fe': 2.08, 'Si': 20.89, 'O': 41.66}
			Density None, Hardness 4.5, Elements {'Na': 4.28, 'Ca': 22.36, 'Mg': 2.71, 'Al': 6.02, 'Fe': 2.08, 'Si': 20.89, 'O': 41.66}
	Found duplicates of "Heklaite", with these properties :
			Density None, Hardness None, Elements {'K': 19.15, 'Na': 11.26, 'Si': 13.76, 'F': 55.83}
			Density None, Hardness None, Elements {'K': 19.15, 'Na': 11.26, 'Si': 13.76, 'F': 55.83}
	Found duplicates of "Adranosite", with these properties :
			Density None, Hardness None, Elements {'Na': 3.81, 'Al': 8.95, 'H': 3.0100000000000002, 'S': 21.28, 'N': 9.29, 'Cl': 5.88, 'O': 47.77}
			Density None, Hardness None, Elements {'Na': 3.81, 'Al': 8.95, 'H': 3.0100000000000002, 'S': 21.28, 'N': 9.29, 'Cl': 5.88, 'O': 47.77}
	Found duplicates of "Kushiroite", with these properties :
			Density None, Hardness None, Elements {'Ca': 18.37, 'Al': 24.74, 'Si': 12.88, 'O': 44.01}
			Density None, Hardness None, Elements {'Ca': 18.37, 'Al': 24.74, 'Si': 12.88, 'O': 44.01}
	Found duplicates of "Cryptophyllite", with these properties :
			Density None, Hardness None, Elements {'K': 16.27, 'Ca': 8.34, 'Si': 23.37, 'H': 2.1, 'O': 49.93}
			Density None, Hardness None, Elements {'K': 16.27, 'Ca': 8.34, 'Si': 23.37, 'H': 2.1, 'O': 49.93}
	Found duplicates of "Shlykovite", with these properties :
			Density None, Hardness None, Elements {'K': 5.26, 'Ca': 5.39, 'Si': 60.43, 'H': 0.95, 'O': 27.97}
			Density None, Hardness None, Elements {'K': 5.26, 'Ca': 5.39, 'Si': 60.43, 'H': 0.95, 'O': 27.97}
	Found duplicates of "Idrialite", with these properties :
			Density 1.236, Hardness 1.5, Elements {'H': 5.07, 'C': 94.93}
			Density 1.236, Hardness 1.5, Elements {'H': 5.07, 'C': 94.93}
			Density 1.236, Hardness 1.5, Elements {'H': 5.07, 'C': 94.93}
			Density 1.236, Hardness 1.5, Elements {'H': 5.07, 'C': 94.93}
	Found duplicates of "Ikaite", with these properties :
			Density 1.78, Hardness None, Elements {'Ca': 19.25, 'H': 5.81, 'C': 5.77, 'O': 69.17}
			Density 1.78, Hardness None, Elements {'Ca': 19.25, 'H': 5.81, 'C': 5.77, 'O': 69.17}
			Density 1.78, Hardness None, Elements {'Ca': 19.25, 'H': 5.81, 'C': 5.77, 'O': 69.17}
			Density 1.78, Hardness None, Elements {'Ca': 19.25, 'H': 5.81, 'C': 5.77, 'O': 69.17}
	Found duplicates of "Ikranite", with these properties :
			Density 2.82, Hardness 5.0, Elements {'K': 0.37, 'Na': 5.93, 'Sr': 1.36, 'Ca': 4.52, 'La': 0.55, 'Ce': 1.32, 'Hf': 0.26, 'Zr': 10.38, 'Ti': 0.23, 'Mn': 2.65, 'Nb': 0.2, 'Fe': 3.68, 'Si': 22.97, 'H': 0.87, 'Nd': 0.16, 'Cl': 0.9, 'O': 43.56, 'F': 0.1}
			Density 2.82, Hardness 5.0, Elements {'K': 0.37, 'Na': 5.93, 'Sr': 1.36, 'Ca': 4.52, 'La': 0.55, 'Ce': 1.32, 'Hf': 0.26, 'Zr': 10.38, 'Ti': 0.23, 'Mn': 2.65, 'Nb': 0.2, 'Fe': 3.68, 'Si': 22.97, 'H': 0.87, 'Nd': 0.16, 'Cl': 0.9, 'O': 43.56, 'F': 0.1}
	Found duplicates of "Allophane", with these properties :
			Density 1.9, Hardness 3.0, Elements {'Al': 23.97, 'Si': 16.22, 'H': 2.24, 'O': 57.57}
			Density 1.9, Hardness 3.0, Elements {'Al': 23.97, 'Si': 16.22, 'H': 2.24, 'O': 57.57}
			Density 1.9, Hardness 3.0, Elements {'Al': 23.97, 'Si': 16.22, 'H': 2.24, 'O': 57.57}
			Density 1.9, Hardness 3.0, Elements {'Al': 23.97, 'Si': 16.22, 'H': 2.24, 'O': 57.57}
	Found duplicates of "Ilinskite", with these properties :
			Density 4.08, Hardness 1.75, Elements {'Na': 3.47, 'Cu': 47.99, 'Se': 23.85, 'Cl': 5.35, 'O': 19.33}
			Density 4.08, Hardness 1.75, Elements {'Na': 3.47, 'Cu': 47.99, 'Se': 23.85, 'Cl': 5.35, 'O': 19.33}
	Found duplicates of "Illite", with these properties :
			Density 2.75, Hardness 1.5, Elements {'K': 6.03, 'Mg': 1.87, 'Al': 9.01, 'Fe': 1.43, 'Si': 25.25, 'H': 1.35, 'O': 55.06}
			Density 2.75, Hardness 1.5, Elements {'K': 6.03, 'Mg': 1.87, 'Al': 9.01, 'Fe': 1.43, 'Si': 25.25, 'H': 1.35, 'O': 55.06}
			Density 2.75, Hardness 1.5, Elements {'K': 6.03, 'Mg': 1.87, 'Al': 9.01, 'Fe': 1.43, 'Si': 25.25, 'H': 1.35, 'O': 55.06}
	Found duplicates of "Iltisite", with these properties :
			Density 6.59, Hardness None, Elements {'Ag': 27.87, 'Hg': 51.82, 'S': 8.28, 'Br': 5.16, 'Cl': 6.87}
			Density 6.59, Hardness None, Elements {'Ag': 27.87, 'Hg': 51.82, 'S': 8.28, 'Br': 5.16, 'Cl': 6.87}
	Found duplicates of "Ilvaite", with these properties :
			Density 4.01, Hardness 5.75, Elements {'Ca': 9.8, 'Fe': 40.98, 'Si': 13.74, 'H': 0.25, 'O': 35.22}
			Density 4.01, Hardness 5.75, Elements {'Ca': 9.8, 'Fe': 40.98, 'Si': 13.74, 'H': 0.25, 'O': 35.22}
			Density 4.01, Hardness 5.75, Elements {'Ca': 9.8, 'Fe': 40.98, 'Si': 13.74, 'H': 0.25, 'O': 35.22}
			Density 4.01, Hardness 5.75, Elements {'Ca': 9.8, 'Fe': 40.98, 'Si': 13.74, 'H': 0.25, 'O': 35.22}
	Found duplicates of "Imogolite", with these properties :
			Density 2.7, Hardness 2.5, Elements {'Al': 27.24, 'Si': 14.18, 'H': 2.04, 'O': 56.54}
			Density 2.7, Hardness 2.5, Elements {'Al': 27.24, 'Si': 14.18, 'H': 2.04, 'O': 56.54}
	Found duplicates of "Anorthite", with these properties :
			Density 2.73, Hardness 6.0, Elements {'Na': 0.41, 'Ca': 13.72, 'Al': 18.97, 'Si': 20.75, 'O': 46.14}
			Density 2.73, Hardness 6.0, Elements {'Na': 0.41, 'Ca': 13.72, 'Al': 18.97, 'Si': 20.75, 'O': 46.14}
	Found duplicates of "Elbaite", with these properties :
			Density 3.05, Hardness 7.5, Elements {'Na': 2.51, 'Li': 1.89, 'Al': 19.13, 'Si': 18.38, 'B': 3.54, 'H': 0.44, 'O': 54.11}
			Density 3.05, Hardness 7.5, Elements {'Na': 2.51, 'Li': 1.89, 'Al': 19.13, 'Si': 18.38, 'B': 3.54, 'H': 0.44, 'O': 54.11}
			Density 3.05, Hardness 7.5, Elements {'Na': 2.51, 'Li': 1.89, 'Al': 19.13, 'Si': 18.38, 'B': 3.54, 'H': 0.44, 'O': 54.11}
			Density 3.05, Hardness 7.5, Elements {'Na': 2.51, 'Li': 1.89, 'Al': 19.13, 'Si': 18.38, 'B': 3.54, 'H': 0.44, 'O': 54.11}
			Density 3.05, Hardness 7.5, Elements {'Na': 2.51, 'Li': 1.89, 'Al': 19.13, 'Si': 18.38, 'B': 3.54, 'H': 0.44, 'O': 54.11}
	Found duplicates of "Inesite", with these properties :
			Density 3.06, Hardness 6.0, Elements {'Ca': 6.08, 'Mn': 29.19, 'Si': 21.31, 'H': 0.92, 'O': 42.5}
			Density 3.06, Hardness 6.0, Elements {'Ca': 6.08, 'Mn': 29.19, 'Si': 21.31, 'H': 0.92, 'O': 42.5}
	Found duplicates of "Intersilite", with these properties :
			Density 2.42, Hardness 3.5, Elements {'Na': 13.19, 'Ti': 4.58, 'Mn': 5.25, 'Si': 26.86, 'H': 1.16, 'O': 48.96}
			Density 2.42, Hardness 3.5, Elements {'Na': 13.19, 'Ti': 4.58, 'Mn': 5.25, 'Si': 26.86, 'H': 1.16, 'O': 48.96}
	Found duplicates of "Iodargyrite", with these properties :
			Density 5.6, Hardness 1.75, Elements {'Ag': 45.95, 'I': 54.05}
			Density 5.6, Hardness 1.75, Elements {'Ag': 45.95, 'I': 54.05}
	Found duplicates of "Cordierite", with these properties :
			Density 2.65, Hardness 7.0, Elements {'Mg': 8.31, 'Al': 18.45, 'Si': 24.01, 'O': 49.23}
			Density 2.65, Hardness 7.0, Elements {'Mg': 8.31, 'Al': 18.45, 'Si': 24.01, 'O': 49.23}
			Density 2.65, Hardness 7.0, Elements {'Mg': 8.31, 'Al': 18.45, 'Si': 24.01, 'O': 49.23}
	Found duplicates of "Ruthenium", with these properties :
			Density 12.2, Hardness 6.5, Elements {'Ir': 41.99, 'Os': 13.85, 'Ru': 44.16}
			Density 12.2, Hardness 6.5, Elements {'Ir': 41.99, 'Os': 13.85, 'Ru': 44.16}
	Found duplicates of "Iron", with these properties :
			Density 7.6, Hardness 4.5, Elements {'Fe': 100.0}
			Density 7.6, Hardness 4.5, Elements {'Fe': 100.0}
			Density 7.6, Hardness 4.5, Elements {'Fe': 100.0}
	Found duplicates of "Isolueshite", with these properties :
			Density 4.72, Hardness 5.5, Elements {'Na': 4.54, 'Ca': 3.96, 'La': 27.45, 'Ti': 5.91, 'Nb': 34.42, 'O': 23.71}
			Density 4.72, Hardness 5.5, Elements {'Na': 4.54, 'Ca': 3.96, 'La': 27.45, 'Ti': 5.91, 'Nb': 34.42, 'O': 23.71}
	Found duplicates of "Isomertieite", with these properties :
			Density 10.33, Hardness 5.5, Elements {'Sb': 15.57, 'As': 9.58, 'Pd': 74.85}
			Density 10.33, Hardness 5.5, Elements {'Sb': 15.57, 'As': 9.58, 'Pd': 74.85}
	Found duplicates of "Isovite", with these properties :
			Density None, Hardness 8.0, Elements {'Cr': 67.41, 'Fe': 26.82, 'C': 5.77}
			Density None, Hardness 8.0, Elements {'Cr': 67.41, 'Fe': 26.82, 'C': 5.77}
	Found duplicates of "Ferrohornblende", with these properties :
			Density 3.23, Hardness 5.5, Elements {'Ca': 8.46, 'Al': 4.98, 'Fe': 25.05, 'Si': 20.75, 'H': 0.21, 'O': 40.53}
			Density 3.23, Hardness 5.5, Elements {'Ca': 8.46, 'Al': 4.98, 'Fe': 25.05, 'Si': 20.75, 'H': 0.21, 'O': 40.53}
			Density 3.23, Hardness 5.5, Elements {'Ca': 8.46, 'Al': 4.98, 'Fe': 25.05, 'Si': 20.75, 'H': 0.21, 'O': 40.53}
			Density 3.23, Hardness 5.5, Elements {'Ca': 8.46, 'Al': 4.98, 'Fe': 25.05, 'Si': 20.75, 'H': 0.21, 'O': 40.53}
	Found duplicates of "Itoigawaite", with these properties :
			Density None, Hardness 5.25, Elements {'Sr': 24.22, 'Al': 14.92, 'Si': 15.53, 'H': 1.11, 'O': 44.22}
			Density None, Hardness 5.25, Elements {'Sr': 24.22, 'Al': 14.92, 'Si': 15.53, 'H': 1.11, 'O': 44.22}
	Found duplicates of "Iwashiroite-Y", with these properties :
			Density None, Hardness 6.0, Elements {'Ca': 0.13, 'Sm': 0.48, 'Gd': 1.0, 'Dy': 2.08, 'Y': 23.0, 'Ho': 0.53, 'Er': 1.6, 'Tm': 0.54, 'Lu': 0.56, 'Th': 0.74, 'Yb': 2.76, 'U': 0.76, 'Ta': 33.51, 'Ti': 0.31, 'Nb': 11.57, 'O': 20.44}
			Density None, Hardness 6.0, Elements {'Ca': 0.13, 'Sm': 0.48, 'Gd': 1.0, 'Dy': 2.08, 'Y': 23.0, 'Ho': 0.53, 'Er': 1.6, 'Tm': 0.54, 'Lu': 0.56, 'Th': 0.74, 'Yb': 2.76, 'U': 0.76, 'Ta': 33.51, 'Ti': 0.31, 'Nb': 11.57, 'O': 20.44}
	Found duplicates of "Izoklakeite", with these properties :
			Density 6.47, Hardness 3.75, Elements {'Fe': 0.21, 'Cu': 0.96, 'Bi': 15.84, 'Sb': 12.69, 'Pb': 52.99, 'S': 17.31}
			Density 6.47, Hardness 3.75, Elements {'Fe': 0.21, 'Cu': 0.96, 'Bi': 15.84, 'Sb': 12.69, 'Pb': 52.99, 'S': 17.31}
	Found duplicates of "Cuprosklodowskite", with these properties :
			Density 3.8, Hardness 4.0, Elements {'U': 55.24, 'Cu': 7.37, 'Si': 6.52, 'H': 1.17, 'O': 29.7}
			Density 3.8, Hardness 4.0, Elements {'U': 55.24, 'Cu': 7.37, 'Si': 6.52, 'H': 1.17, 'O': 29.7}
	Found duplicates of "Jachymovite", with these properties :
			Density 4.79, Hardness None, Elements {'U': 69.79, 'H': 1.48, 'S': 1.18, 'O': 27.56}
			Density 4.79, Hardness None, Elements {'U': 69.79, 'H': 1.48, 'S': 1.18, 'O': 27.56}
	Found duplicates of "Jacquesdietrichite", with these properties :
			Density 3.28, Hardness 2.0, Elements {'Cu': 53.19, 'B': 4.52, 'H': 2.11, 'O': 40.18}
			Density 3.28, Hardness 2.0, Elements {'Cu': 53.19, 'B': 4.52, 'H': 2.11, 'O': 40.18}
	Found duplicates of "Jadarite", with these properties :
			Density 2.45, Hardness None, Elements {'Na': 11.1, 'Li': 3.38, 'Si': 12.29, 'B': 14.63, 'H': 0.48, 'O': 58.11}
			Density 2.45, Hardness None, Elements {'Na': 11.1, 'Li': 3.38, 'Si': 12.29, 'B': 14.63, 'H': 0.48, 'O': 58.11}
	Found duplicates of "Jagowerite", with these properties :
			Density 4.01, Hardness 4.5, Elements {'Ba': 33.07, 'Al': 13.0, 'P': 14.92, 'H': 0.49, 'O': 38.53}
			Density 4.01, Hardness 4.5, Elements {'Ba': 33.07, 'Al': 13.0, 'P': 14.92, 'H': 0.49, 'O': 38.53}
	Found duplicates of "Jagueite", with these properties :
			Density None, Hardness 5.0, Elements {'Cu': 15.8, 'Ag': 1.54, 'Pd': 42.26, 'Se': 40.4}
			Density None, Hardness 5.0, Elements {'Cu': 15.8, 'Ag': 1.54, 'Pd': 42.26, 'Se': 40.4}
	Found duplicates of "Jahnsite-CaMnMg", with these properties :
			Density 2.71, Hardness 4.0, Elements {'Ca': 4.93, 'Mg': 5.98, 'Mn': 6.75, 'Fe': 13.73, 'P': 15.23, 'H': 2.23, 'O': 51.15}
			Density 2.71, Hardness 4.0, Elements {'Ca': 4.93, 'Mg': 5.98, 'Mn': 6.75, 'Fe': 13.73, 'P': 15.23, 'H': 2.23, 'O': 51.15}
	Found duplicates of "Jahnsite-CaMnMn", with these properties :
			Density 2.78, Hardness 4.0, Elements {'Ca': 4.58, 'Mn': 18.84, 'Fe': 12.77, 'P': 14.17, 'H': 2.07, 'O': 47.56}
			Density 2.78, Hardness 4.0, Elements {'Ca': 4.58, 'Mn': 18.84, 'Fe': 12.77, 'P': 14.17, 'H': 2.07, 'O': 47.56}
	Found duplicates of "Jahnsite-NaMnMg", with these properties :
			Density 2.58, Hardness 4.0, Elements {'Na': 2.89, 'Mg': 6.1, 'Mn': 6.9, 'Fe': 14.03, 'P': 15.56, 'H': 2.28, 'O': 52.24}
			Density 2.58, Hardness 4.0, Elements {'Na': 2.89, 'Mg': 6.1, 'Mn': 6.9, 'Fe': 14.03, 'P': 15.56, 'H': 2.28, 'O': 52.24}
	Found duplicates of "Jamborite", with these properties :
			Density 2.67, Hardness None, Elements {'Fe': 5.03, 'Ni': 47.57, 'H': 2.72, 'S': 2.89, 'O': 41.79}
			Density 2.67, Hardness None, Elements {'Fe': 5.03, 'Ni': 47.57, 'H': 2.72, 'S': 2.89, 'O': 41.79}
	Found duplicates of "Jamesonite", with these properties :
			Density 5.56, Hardness 2.5, Elements {'Fe': 2.71, 'Sb': 35.39, 'Pb': 40.15, 'S': 21.75}
			Density 5.56, Hardness 2.5, Elements {'Fe': 2.71, 'Sb': 35.39, 'Pb': 40.15, 'S': 21.75}
	Found duplicates of "Jankovicite", with these properties :
			Density 5.08, Hardness 2.0, Elements {'Tl': 32.24, 'Sb': 38.41, 'As': 7.09, 'S': 22.26}
			Density 5.08, Hardness 2.0, Elements {'Tl': 32.24, 'Sb': 38.41, 'As': 7.09, 'S': 22.26}
	Found duplicates of "Jarandolite", with these properties :
			Density None, Hardness 6.0, Elements {'Ca': 21.83, 'Si': 0.15, 'B': 17.26, 'H': 1.5, 'Cl': 0.19, 'O': 59.07}
			Density None, Hardness 6.0, Elements {'Ca': 21.83, 'Si': 0.15, 'B': 17.26, 'H': 1.5, 'Cl': 0.19, 'O': 59.07}
			Density None, Hardness 6.0, Elements {'Ca': 21.83, 'Si': 0.15, 'B': 17.26, 'H': 1.5, 'Cl': 0.19, 'O': 59.07}
			Density None, Hardness 6.0, Elements {'Ca': 21.83, 'Si': 0.15, 'B': 17.26, 'H': 1.5, 'Cl': 0.19, 'O': 59.07}
	Found duplicates of "Zircon", with these properties :
			Density 4.65, Hardness 7.5, Elements {'RE': 3.78, 'Hf': 4.69, 'Zr': 43.14, 'Si': 14.76, 'O': 33.63}
			Density 4.65, Hardness 7.5, Elements {'RE': 3.78, 'Hf': 4.69, 'Zr': 43.14, 'Si': 14.76, 'O': 33.63}
			Density 4.65, Hardness 7.5, Elements {'RE': 3.78, 'Hf': 4.69, 'Zr': 43.14, 'Si': 14.76, 'O': 33.63}
			Density 4.65, Hardness 7.5, Elements {'RE': 3.78, 'Hf': 4.69, 'Zr': 43.14, 'Si': 14.76, 'O': 33.63}
			Density 4.65, Hardness 7.5, Elements {'RE': 3.78, 'Hf': 4.69, 'Zr': 43.14, 'Si': 14.76, 'O': 33.63}
			Density 4.65, Hardness 7.5, Elements {'RE': 3.78, 'Hf': 4.69, 'Zr': 43.14, 'Si': 14.76, 'O': 33.63}
	Found duplicates of "Jedwabite", with these properties :
			Density 8.6, Hardness 7.0, Elements {'Ta': 38.29, 'Nb': 6.55, 'Fe': 55.15}
			Density 8.6, Hardness 7.0, Elements {'Ta': 38.29, 'Nb': 6.55, 'Fe': 55.15}
			Density 8.6, Hardness 7.0, Elements {'Ta': 38.29, 'Nb': 6.55, 'Fe': 55.15}
	Found duplicates of "Hydrobiotite", with these properties :
			Density 2.56, Hardness 2.75, Elements {'K': 2.52, 'Ca': 0.86, 'Mg': 12.03, 'Al': 6.97, 'Fe': 7.21, 'Si': 16.93, 'H': 1.69, 'O': 50.96, 'F': 0.82}
			Density 2.56, Hardness 2.75, Elements {'K': 2.52, 'Ca': 0.86, 'Mg': 12.03, 'Al': 6.97, 'Fe': 7.21, 'Si': 16.93, 'H': 1.69, 'O': 50.96, 'F': 0.82}
			Density 2.56, Hardness 2.75, Elements {'K': 2.52, 'Ca': 0.86, 'Mg': 12.03, 'Al': 6.97, 'Fe': 7.21, 'Si': 16.93, 'H': 1.69, 'O': 50.96, 'F': 0.82}
	Found duplicates of "Vermiculite", with these properties :
			Density 2.5, Hardness 1.75, Elements {'Mg': 8.68, 'Al': 23.01, 'Fe': 9.97, 'Si': 5.57, 'H': 2.0, 'O': 50.77}
			Density 2.5, Hardness 1.75, Elements {'Mg': 8.68, 'Al': 23.01, 'Fe': 9.97, 'Si': 5.57, 'H': 2.0, 'O': 50.77}
			Density 2.5, Hardness 1.75, Elements {'Mg': 8.68, 'Al': 23.01, 'Fe': 9.97, 'Si': 5.57, 'H': 2.0, 'O': 50.77}
			Density 2.5, Hardness 1.75, Elements {'Mg': 8.68, 'Al': 23.01, 'Fe': 9.97, 'Si': 5.57, 'H': 2.0, 'O': 50.77}
	Found duplicates of "Jeffreyite", with these properties :
			Density 2.99, Hardness 5.0, Elements {'Na': 4.5, 'Ca': 23.55, 'Be': 2.65, 'Al': 2.64, 'Si': 22.0, 'H': 0.79, 'O': 43.87}
			Density 2.99, Hardness 5.0, Elements {'Na': 4.5, 'Ca': 23.55, 'Be': 2.65, 'Al': 2.64, 'Si': 22.0, 'H': 0.79, 'O': 43.87}
	Found duplicates of "Antigorite", with these properties :
			Density 2.54, Hardness 3.75, Elements {'Mg': 18.18, 'Fe': 13.93, 'Si': 18.68, 'H': 1.34, 'O': 47.88}
			Density 2.54, Hardness 3.75, Elements {'Mg': 18.18, 'Fe': 13.93, 'Si': 18.68, 'H': 1.34, 'O': 47.88}
			Density 2.54, Hardness 3.75, Elements {'Mg': 18.18, 'Fe': 13.93, 'Si': 18.68, 'H': 1.34, 'O': 47.88}
			Density 2.54, Hardness 3.75, Elements {'Mg': 18.18, 'Fe': 13.93, 'Si': 18.68, 'H': 1.34, 'O': 47.88}
			Density 2.54, Hardness 3.75, Elements {'Mg': 18.18, 'Fe': 13.93, 'Si': 18.68, 'H': 1.34, 'O': 47.88}
			Density 2.54, Hardness 3.75, Elements {'Mg': 18.18, 'Fe': 13.93, 'Si': 18.68, 'H': 1.34, 'O': 47.88}
	Found duplicates of "Jensenite", with these properties :
			Density 4.76, Hardness 3.5, Elements {'Cu': 42.34, 'Te': 28.34, 'H': 0.9, 'O': 28.43}
			Density 4.76, Hardness 3.5, Elements {'Cu': 42.34, 'Te': 28.34, 'H': 0.9, 'O': 28.43}
	Found duplicates of "Jentschite", with these properties :
			Density 5.24, Hardness 2.25, Elements {'Tl': 23.34, 'Sb': 13.91, 'As': 17.11, 'Pb': 23.66, 'S': 21.97}
			Density 5.24, Hardness 2.25, Elements {'Tl': 23.34, 'Sb': 13.91, 'As': 17.11, 'Pb': 23.66, 'S': 21.97}
	Found duplicates of "Jeremejevite", with these properties :
			Density 3.29, Hardness 7.0, Elements {'Al': 31.62, 'B': 10.56, 'H': 0.1, 'O': 48.44, 'F': 9.28}
			Density 3.29, Hardness 7.0, Elements {'Al': 31.62, 'B': 10.56, 'H': 0.1, 'O': 48.44, 'F': 9.28}
			Density 3.29, Hardness 7.0, Elements {'Al': 31.62, 'B': 10.56, 'H': 0.1, 'O': 48.44, 'F': 9.28}
	Found duplicates of "Jianshuiite", with these properties :
			Density 3.55, Hardness 1.75, Elements {'Ca': 1.09, 'Mg': 3.31, 'Mn': 50.9, 'H': 1.54, 'O': 43.16}
			Density 3.55, Hardness 1.75, Elements {'Ca': 1.09, 'Mg': 3.31, 'Mn': 50.9, 'H': 1.54, 'O': 43.16}
	Found duplicates of "Jinshajiangite", with these properties :
			Density 3.61, Hardness 4.75, Elements {'K': 1.99, 'Ba': 8.53, 'Na': 2.34, 'Ca': 2.26, 'Zr': 0.52, 'Ti': 9.73, 'Mn': 10.24, 'Nb': 0.79, 'Fe': 16.08, 'Si': 12.69, 'H': 0.07, 'O': 32.08, 'F': 2.68}
			Density 3.61, Hardness 4.75, Elements {'K': 1.99, 'Ba': 8.53, 'Na': 2.34, 'Ca': 2.26, 'Zr': 0.52, 'Ti': 9.73, 'Mn': 10.24, 'Nb': 0.79, 'Fe': 16.08, 'Si': 12.69, 'H': 0.07, 'O': 32.08, 'F': 2.68}
	Found duplicates of "Johnsenite-Ce", with these properties :
			Density 3.2, Hardness 5.5, Elements {'K': 0.22, 'Na': 8.0, 'Sr': 1.4, 'Ca': 6.62, 'La': 1.36, 'Ce': 2.66, 'Pr': 1.0, 'Sm': 0.09, 'Gd': 0.28, 'Dy': 0.14, 'Y': 0.58, 'Hf': 0.05, 'Zr': 7.33, 'Ti': 0.45, 'Mn': 4.33, 'Nb': 0.58, 'Fe': 1.29, 'Si': 20.78, 'H': 0.04, 'W': 4.25, 'C': 0.36, 'Nd': 0.77, 'Cl': 0.79, 'O': 36.63}
			Density 3.2, Hardness 5.5, Elements {'K': 0.22, 'Na': 8.0, 'Sr': 1.4, 'Ca': 6.62, 'La': 1.36, 'Ce': 2.66, 'Pr': 1.0, 'Sm': 0.09, 'Gd': 0.28, 'Dy': 0.14, 'Y': 0.58, 'Hf': 0.05, 'Zr': 7.33, 'Ti': 0.45, 'Mn': 4.33, 'Nb': 0.58, 'Fe': 1.29, 'Si': 20.78, 'H': 0.04, 'W': 4.25, 'C': 0.36, 'Nd': 0.77, 'Cl': 0.79, 'O': 36.63}
	Found duplicates of "Johntomaite", with these properties :
			Density 4.05, Hardness 4.5, Elements {'Ba': 19.9, 'Ca': 2.32, 'Mn': 2.39, 'Fe': 26.71, 'P': 13.47, 'H': 0.44, 'O': 34.78}
			Density 4.05, Hardness 4.5, Elements {'Ba': 19.9, 'Ca': 2.32, 'Mn': 2.39, 'Fe': 26.71, 'P': 13.47, 'H': 0.44, 'O': 34.78}
	Found duplicates of "Jolliffeite", with these properties :
			Density 7.1, Hardness 6.25, Elements {'Co': 6.93, 'Ni': 20.7, 'As': 35.24, 'Se': 37.13}
			Density 7.1, Hardness 6.25, Elements {'Co': 6.93, 'Ni': 20.7, 'As': 35.24, 'Se': 37.13}
	Found duplicates of "Jonassonite", with these properties :
			Density None, Hardness 2.75, Elements {'Cd': 0.08, 'Ag': 0.08, 'Bi': 68.68, 'Sb': 0.09, 'Pb': 6.01, 'Au': 14.94, 'Se': 0.41, 'S': 9.7}
			Density None, Hardness 2.75, Elements {'Cd': 0.08, 'Ag': 0.08, 'Bi': 68.68, 'Sb': 0.09, 'Pb': 6.01, 'Au': 14.94, 'Se': 0.41, 'S': 9.7}
	Found duplicates of "Joosteite", with these properties :
			Density None, Hardness None, Elements {'Mn': 29.79, 'Fe': 3.36, 'P': 18.66, 'O': 48.19}
			Density None, Hardness None, Elements {'Mn': 29.79, 'Fe': 3.36, 'P': 18.66, 'O': 48.19}
	Found duplicates of "Jordanite", with these properties :
			Density 5.95, Hardness 3.0, Elements {'Sb': 5.82, 'As': 7.17, 'Pb': 69.37, 'S': 17.64}
			Density 5.95, Hardness 3.0, Elements {'Sb': 5.82, 'As': 7.17, 'Pb': 69.37, 'S': 17.64}
	Found duplicates of "Jorgensenite", with these properties :
			Density 3.89, Hardness 3.75, Elements {'Ba': 15.76, 'Na': 3.02, 'Sr': 30.17, 'Al': 10.62, 'H': 0.02, 'O': 0.39, 'F': 40.02}
			Density 3.89, Hardness 3.75, Elements {'Ba': 15.76, 'Na': 3.02, 'Sr': 30.17, 'Al': 10.62, 'H': 0.02, 'O': 0.39, 'F': 40.02}
	Found duplicates of "Awaruite", with these properties :
			Density 8.0, Hardness 5.0, Elements {'Fe': 27.57, 'Ni': 72.43}
			Density 8.0, Hardness 5.0, Elements {'Fe': 27.57, 'Ni': 72.43}
			Density 8.0, Hardness 5.0, Elements {'Fe': 27.57, 'Ni': 72.43}
			Density 8.0, Hardness 5.0, Elements {'Fe': 27.57, 'Ni': 72.43}
	Found duplicates of "Juabite", with these properties :
			Density 4.59, Hardness 3.5, Elements {'Ca': 1.81, 'Fe': 0.22, 'Cu': 31.14, 'Te': 25.01, 'As': 14.68, 'H': 0.49, 'O': 26.65}
			Density 4.59, Hardness 3.5, Elements {'Ca': 1.81, 'Fe': 0.22, 'Cu': 31.14, 'Te': 25.01, 'As': 14.68, 'H': 0.49, 'O': 26.65}
	Found duplicates of "Juangodoyite", with these properties :
			Density None, Hardness None, Elements {'Na': 20.83, 'Cu': 27.13, 'C': 10.41, 'O': 41.62}
			Density None, Hardness None, Elements {'Na': 20.83, 'Cu': 27.13, 'C': 10.41, 'O': 41.62}
	Found duplicates of "Juanitaite", with these properties :
			Density 3.61, Hardness 1.0, Elements {'Ca': 6.42, 'Fe': 1.79, 'Cu': 28.5, 'Bi': 13.39, 'As': 19.2, 'H': 0.97, 'O': 29.73}
			Density 3.61, Hardness 1.0, Elements {'Ca': 6.42, 'Fe': 1.79, 'Cu': 28.5, 'Bi': 13.39, 'As': 19.2, 'H': 0.97, 'O': 29.73}
	Found duplicates of "Julgoldite-Fe++", with these properties :
			Density 3.602, Hardness 4.5, Elements {'Ca': 14.69, 'Al': 2.47, 'Fe': 25.59, 'Si': 15.44, 'H': 0.74, 'O': 41.06}
			Density 3.602, Hardness 4.5, Elements {'Ca': 14.69, 'Al': 2.47, 'Fe': 25.59, 'Si': 15.44, 'H': 0.74, 'O': 41.06}
	Found duplicates of "Tennantite", with these properties :
			Density 4.65, Hardness 3.75, Elements {'Fe': 3.8, 'Cu': 47.51, 'As': 20.37, 'S': 28.33}
			Density 4.65, Hardness 3.75, Elements {'Fe': 3.8, 'Cu': 47.51, 'As': 20.37, 'S': 28.33}
	Found duplicates of "Julienite", with these properties :
			Density 1.648, Hardness None, Elements {'Na': 9.55, 'Co': 12.24, 'H': 3.35, 'C': 9.98, 'S': 26.65, 'N': 11.64, 'O': 26.59}
			Density 1.648, Hardness None, Elements {'Na': 9.55, 'Co': 12.24, 'H': 3.35, 'C': 9.98, 'S': 26.65, 'N': 11.64, 'O': 26.59}
	Found duplicates of "Juonniite", with these properties :
			Density 2.43, Hardness 4.25, Elements {'Ca': 10.32, 'Mg': 6.26, 'Sc': 11.58, 'P': 15.95, 'H': 2.34, 'O': 53.56}
			Density 2.43, Hardness 4.25, Elements {'Ca': 10.32, 'Mg': 6.26, 'Sc': 11.58, 'P': 15.95, 'H': 2.34, 'O': 53.56}
	Found duplicates of "Xonotlite", with these properties :
			Density 2.7, Hardness 6.5, Elements {'Ca': 33.63, 'Si': 23.57, 'H': 0.28, 'O': 42.52}
			Density 2.7, Hardness 6.5, Elements {'Ca': 33.63, 'Si': 23.57, 'H': 0.28, 'O': 42.52}
			Density 2.7, Hardness 6.5, Elements {'Ca': 33.63, 'Si': 23.57, 'H': 0.28, 'O': 42.52}
	Found duplicates of "Kainosite-Y", with these properties :
			Density 3.5, Hardness 5.5, Elements {'Ca': 12.07, 'Ce': 10.55, 'Y': 20.08, 'Si': 16.92, 'H': 0.58, 'C': 1.27, 'O': 38.54}
			Density 3.5, Hardness 5.5, Elements {'Ca': 12.07, 'Ce': 10.55, 'Y': 20.08, 'Si': 16.92, 'H': 0.58, 'C': 1.27, 'O': 38.54}
	Found duplicates of "Cacoxenite", with these properties :
			Density 2.84, Hardness 3.25, Elements {'Al': 3.76, 'Fe': 23.37, 'P': 11.75, 'H': 3.64, 'O': 57.48}
			Density 2.84, Hardness 3.25, Elements {'Al': 3.76, 'Fe': 23.37, 'P': 11.75, 'H': 3.64, 'O': 57.48}
	Found duplicates of "Kalifersite", with these properties :
			Density 2.32, Hardness 2.0, Elements {'K': 6.1, 'Na': 1.54, 'Fe': 17.44, 'Si': 25.05, 'H': 1.35, 'O': 48.52}
			Density 2.32, Hardness 2.0, Elements {'K': 6.1, 'Na': 1.54, 'Fe': 17.44, 'Si': 25.05, 'H': 1.35, 'O': 48.52}
	Found duplicates of "Kaliophilite", with these properties :
			Density 2.58, Hardness 5.75, Elements {'K': 24.72, 'Al': 17.06, 'Si': 17.76, 'O': 40.46}
			Density 2.58, Hardness 5.75, Elements {'K': 24.72, 'Al': 17.06, 'Si': 17.76, 'O': 40.46}
			Density 2.58, Hardness 5.75, Elements {'K': 24.72, 'Al': 17.06, 'Si': 17.76, 'O': 40.46}
	Found duplicates of "Kalungaite", with these properties :
			Density None, Hardness None, Elements {'Bi': 0.32, 'Sb': 1.6, 'As': 27.58, 'Pd': 41.48, 'Se': 27.78, 'S': 1.23}
			Density None, Hardness None, Elements {'Bi': 0.32, 'Sb': 1.6, 'As': 27.58, 'Pd': 41.48, 'Se': 27.78, 'S': 1.23}
	Found duplicates of "Kampfite", with these properties :
			Density None, Hardness 3.0, Elements {'Ba': 50.96, 'Na': 0.04, 'Sr': 0.08, 'Ti': 0.07, 'Al': 4.28, 'Si': 9.26, 'C': 2.94, 'Cl': 5.35, 'O': 27.01}
			Density None, Hardness 3.0, Elements {'Ba': 50.96, 'Na': 0.04, 'Sr': 0.08, 'Ti': 0.07, 'Al': 4.28, 'Si': 9.26, 'C': 2.94, 'Cl': 5.35, 'O': 27.01}
	Found duplicates of "Kamphaugite-Y", with these properties :
			Density 3.19, Hardness 2.5, Elements {'Ca': 12.6, 'RE': 13.28, 'Y': 22.17, 'H': 0.97, 'C': 8.21, 'O': 42.77}
			Density 3.19, Hardness 2.5, Elements {'Ca': 12.6, 'RE': 13.28, 'Y': 22.17, 'H': 0.97, 'C': 8.21, 'O': 42.77}
	Found duplicates of "Kanonerovite", with these properties :
			Density 1.91, Hardness 2.75, Elements {'Na': 11.28, 'Mn': 9.3, 'P': 15.73, 'H': 4.13, 'O': 59.57}
			Density 1.91, Hardness 2.75, Elements {'Na': 11.28, 'Mn': 9.3, 'P': 15.73, 'H': 4.13, 'O': 59.57}
	Found duplicates of "Kaolinite", with these properties :
			Density 2.6, Hardness 1.75, Elements {'Al': 20.9, 'Si': 21.76, 'H': 1.56, 'O': 55.78}
			Density 2.6, Hardness 1.75, Elements {'Al': 20.9, 'Si': 21.76, 'H': 1.56, 'O': 55.78}
	Found duplicates of "Kapellasite", with these properties :
			Density 3.55, Hardness None, Elements {'Zn': 11.43, 'Cu': 47.99, 'H': 1.4, 'Cl': 17.02, 'O': 22.15}
			Density 3.55, Hardness None, Elements {'Zn': 11.43, 'Cu': 47.99, 'H': 1.4, 'Cl': 17.02, 'O': 22.15}
	Found duplicates of "Kapitsaite-Y", with these properties :
			Density 3.74, Hardness 5.5, Elements {'K': 0.83, 'Ba': 33.85, 'Na': 0.16, 'Ca': 1.98, 'RE': 2.03, 'Y': 6.26, 'Si': 16.42, 'B': 2.66, 'Pb': 2.92, 'O': 31.55, 'F': 1.34}
			Density 3.74, Hardness 5.5, Elements {'K': 0.83, 'Ba': 33.85, 'Na': 0.16, 'Ca': 1.98, 'RE': 2.03, 'Y': 6.26, 'Si': 16.42, 'B': 2.66, 'Pb': 2.92, 'O': 31.55, 'F': 1.34}
	Found duplicates of "Kapustinite", with these properties :
			Density 2.78, Hardness 6.0, Elements {'Na': 17.94, 'Ca': 0.12, 'Ce': 0.2, 'Y': 0.13, 'Zr': 12.04, 'U': 0.35, 'Ti': 0.35, 'Mn': 1.83, 'Fe': 0.24, 'Si': 24.44, 'H': 0.31, 'Nd': 0.21, 'O': 41.84}
			Density 2.78, Hardness 6.0, Elements {'Na': 17.94, 'Ca': 0.12, 'Ce': 0.2, 'Y': 0.13, 'Zr': 12.04, 'U': 0.35, 'Ti': 0.35, 'Mn': 1.83, 'Fe': 0.24, 'Si': 24.44, 'H': 0.31, 'Nd': 0.21, 'O': 41.84}
	Found duplicates of "Karasugite", with these properties :
			Density 3.206, Hardness None, Elements {'Sr': 30.83, 'Ca': 14.1, 'Al': 9.49, 'H': 0.62, 'O': 9.85, 'F': 35.1}
			Density 3.206, Hardness None, Elements {'Sr': 30.83, 'Ca': 14.1, 'Al': 9.49, 'H': 0.62, 'O': 9.85, 'F': 35.1}
	Found duplicates of "Karchevskyite", with these properties :
			Density 2.21, Hardness 2.0, Elements {'Sr': 6.25, 'Ca': 0.14, 'Mg': 17.91, 'Al': 9.68, 'P': 0.57, 'H': 3.2, 'C': 3.96, 'O': 58.28}
			Density 2.21, Hardness 2.0, Elements {'Sr': 6.25, 'Ca': 0.14, 'Mg': 17.91, 'Al': 9.68, 'P': 0.57, 'H': 3.2, 'C': 3.96, 'O': 58.28}
	Found duplicates of "Karupmollerite-Ca", with these properties :
			Density 2.71, Hardness 5.0, Elements {'K': 1.42, 'Ba': 0.23, 'Na': 1.59, 'Sr': 0.07, 'Ca': 4.23, 'Ti': 5.06, 'Mn': 0.23, 'Nb': 20.71, 'Al': 0.13, 'Zn': 0.05, 'Fe': 0.32, 'Si': 18.48, 'H': 1.25, 'O': 46.21}
			Density 2.71, Hardness 5.0, Elements {'K': 1.42, 'Ba': 0.23, 'Na': 1.59, 'Sr': 0.07, 'Ca': 4.23, 'Ti': 5.06, 'Mn': 0.23, 'Nb': 20.71, 'Al': 0.13, 'Zn': 0.05, 'Fe': 0.32, 'Si': 18.48, 'H': 1.25, 'O': 46.21}
	Found duplicates of "Caryinite", with these properties :
			Density 4.29, Hardness 4.0, Elements {'Na': 3.37, 'Ca': 7.65, 'Mg': 1.78, 'Mn': 14.51, 'As': 31.89, 'P': 0.45, 'Pb': 12.16, 'O': 28.18}
			Density 4.29, Hardness 4.0, Elements {'Na': 3.37, 'Ca': 7.65, 'Mg': 1.78, 'Mn': 14.51, 'As': 31.89, 'P': 0.45, 'Pb': 12.16, 'O': 28.18}
	Found duplicates of "Kastningite", with these properties :
			Density None, Hardness None, Elements {'Mg': 0.26, 'Mn': 7.51, 'Al': 11.34, 'Fe': 3.52, 'P': 13.02, 'H': 3.81, 'O': 60.54}
			Density None, Hardness None, Elements {'Mg': 0.26, 'Mn': 7.51, 'Al': 11.34, 'Fe': 3.52, 'P': 13.02, 'H': 3.81, 'O': 60.54}
	Found duplicates of "Plancheite", with these properties :
			Density 3.7, Hardness 5.5, Elements {'Cu': 43.41, 'Si': 19.19, 'H': 0.52, 'O': 36.89}
			Density 3.7, Hardness 5.5, Elements {'Cu': 43.41, 'Si': 19.19, 'H': 0.52, 'O': 36.89}
			Density 3.7, Hardness 5.5, Elements {'Cu': 43.41, 'Si': 19.19, 'H': 0.52, 'O': 36.89}
			Density 3.7, Hardness 5.5, Elements {'Cu': 43.41, 'Si': 19.19, 'H': 0.52, 'O': 36.89}
	Found duplicates of "Katoite", with these properties :
			Density 2.76, Hardness 5.5, Elements {'Ca': 29.02, 'Al': 13.02, 'Si': 10.17, 'H': 1.46, 'O': 46.33}
			Density 2.76, Hardness 5.5, Elements {'Ca': 29.02, 'Al': 13.02, 'Si': 10.17, 'H': 1.46, 'O': 46.33}
	Found duplicates of "Katophorite", with these properties :
			Density 3.35, Hardness 5.0, Elements {'K': 1.25, 'Na': 2.94, 'Ca': 5.54, 'Mg': 3.88, 'Ti': 1.02, 'Mn': 1.17, 'Al': 2.01, 'Fe': 19.31, 'Si': 21.81, 'H': 0.21, 'O': 40.86}
			Density 3.35, Hardness 5.0, Elements {'K': 1.25, 'Na': 2.94, 'Ca': 5.54, 'Mg': 3.88, 'Ti': 1.02, 'Mn': 1.17, 'Al': 2.01, 'Fe': 19.31, 'Si': 21.81, 'H': 0.21, 'O': 40.86}
	Found duplicates of "Katoptrite", with these properties :
			Density 4.56, Hardness 5.5, Elements {'Mg': 2.19, 'Mn': 41.37, 'Al': 5.73, 'Fe': 1.8, 'Si': 3.62, 'Sb': 16.46, 'O': 28.84}
			Density 4.56, Hardness 5.5, Elements {'Mg': 2.19, 'Mn': 41.37, 'Al': 5.73, 'Fe': 1.8, 'Si': 3.62, 'Sb': 16.46, 'O': 28.84}
	Found duplicates of "Woodhouseite", with these properties :
			Density 3.012, Hardness 4.5, Elements {'Ca': 9.68, 'Al': 19.55, 'P': 7.48, 'H': 1.46, 'S': 7.74, 'O': 54.09}
			Density 3.012, Hardness 4.5, Elements {'Ca': 9.68, 'Al': 19.55, 'P': 7.48, 'H': 1.46, 'S': 7.74, 'O': 54.09}
	Found duplicates of "Titanite", with these properties :
			Density 3.48, Hardness 5.25, Elements {'Ca': 19.25, 'RE': 3.64, 'Ti': 18.16, 'Al': 2.73, 'Fe': 1.41, 'Si': 14.2, 'O': 39.64, 'F': 0.96}
			Density 3.48, Hardness 5.25, Elements {'Ca': 19.25, 'RE': 3.64, 'Ti': 18.16, 'Al': 2.73, 'Fe': 1.41, 'Si': 14.2, 'O': 39.64, 'F': 0.96}
			Density 3.48, Hardness 5.25, Elements {'Ca': 19.25, 'RE': 3.64, 'Ti': 18.16, 'Al': 2.73, 'Fe': 1.41, 'Si': 14.2, 'O': 39.64, 'F': 0.96}
			Density 3.48, Hardness 5.25, Elements {'Ca': 19.25, 'RE': 3.64, 'Ti': 18.16, 'Al': 2.73, 'Fe': 1.41, 'Si': 14.2, 'O': 39.64, 'F': 0.96}
			Density 3.48, Hardness 5.25, Elements {'Ca': 19.25, 'RE': 3.64, 'Ti': 18.16, 'Al': 2.73, 'Fe': 1.41, 'Si': 14.2, 'O': 39.64, 'F': 0.96}
	Found duplicates of "Keilite", with these properties :
			Density None, Hardness None, Elements {'Ca': 0.83, 'Mg': 4.75, 'Mn': 23.88, 'Zn': 0.08, 'Cr': 1.21, 'Fe': 29.8, 'S': 39.46}
			Density None, Hardness None, Elements {'Ca': 0.83, 'Mg': 4.75, 'Mn': 23.88, 'Zn': 0.08, 'Cr': 1.21, 'Fe': 29.8, 'S': 39.46}
	Found duplicates of "Keithconnite", with these properties :
			Density None, Hardness 5.0, Elements {'Te': 29.98, 'Pd': 70.02}
			Density None, Hardness 5.0, Elements {'Te': 29.98, 'Pd': 70.02}
	Found duplicates of "Kenhsuite", with these properties :
			Density 6.76, Hardness 2.5, Elements {'Hg': 81.67, 'S': 8.7, 'Cl': 9.62}
			Density 6.76, Hardness 2.5, Elements {'Hg': 81.67, 'S': 8.7, 'Cl': 9.62}
	Found duplicates of "Armalcolite", with these properties :
			Density 4.0, Hardness 6.0, Elements {'Mg': 8.77, 'Ti': 46.05, 'Fe': 6.71, 'O': 38.47}
			Density 4.0, Hardness 6.0, Elements {'Mg': 8.77, 'Ti': 46.05, 'Fe': 6.71, 'O': 38.47}
	Found duplicates of "Kentbrooksite", with these properties :
			Density 3.08, Hardness 5.5, Elements {'Na': 5.3, 'Ca': 4.11, 'RE': 29.53, 'Zr': 7.02, 'Mn': 1.41, 'Nb': 2.38, 'Si': 18.0, 'H': 0.1, 'O': 31.17, 'F': 0.97}
			Density 3.08, Hardness 5.5, Elements {'Na': 5.3, 'Ca': 4.11, 'RE': 29.53, 'Zr': 7.02, 'Mn': 1.41, 'Nb': 2.38, 'Si': 18.0, 'H': 0.1, 'O': 31.17, 'F': 0.97}
	Found duplicates of "Alunogen", with these properties :
			Density 1.71, Hardness 1.75, Elements {'Al': 8.32, 'H': 5.29, 'S': 14.84, 'O': 71.56}
			Density 1.71, Hardness 1.75, Elements {'Al': 8.32, 'H': 5.29, 'S': 14.84, 'O': 71.56}
			Density 1.71, Hardness 1.75, Elements {'Al': 8.32, 'H': 5.29, 'S': 14.84, 'O': 71.56}
			Density 1.71, Hardness 1.75, Elements {'Al': 8.32, 'H': 5.29, 'S': 14.84, 'O': 71.56}
	Found duplicates of "Kermesite", with these properties :
			Density 4.55, Hardness 1.75, Elements {'Sb': 75.24, 'S': 19.82, 'O': 4.94}
			Density 4.55, Hardness 1.75, Elements {'Sb': 75.24, 'S': 19.82, 'O': 4.94}
	Found duplicates of "Kernite", with these properties :
			Density 1.91, Hardness 2.75, Elements {'Na': 15.84, 'B': 14.9, 'H': 3.13, 'O': 66.14}
			Density 1.91, Hardness 2.75, Elements {'Na': 15.84, 'B': 14.9, 'H': 3.13, 'O': 66.14}
	Found duplicates of "Pimelite", with these properties :
			Density 2.5, Hardness 2.5, Elements {'Si': 20.26, 'Ni': 31.75, 'H': 1.82, 'O': 46.17}
			Density 2.5, Hardness 2.5, Elements {'Si': 20.26, 'Ni': 31.75, 'H': 1.82, 'O': 46.17}
			Density 2.5, Hardness 2.5, Elements {'Si': 20.26, 'Ni': 31.75, 'H': 1.82, 'O': 46.17}
			Density 2.5, Hardness 2.5, Elements {'Si': 20.26, 'Ni': 31.75, 'H': 1.82, 'O': 46.17}
			Density 2.5, Hardness 2.5, Elements {'Si': 20.26, 'Ni': 31.75, 'H': 1.82, 'O': 46.17}
	Found duplicates of "Talc", with these properties :
			Density 2.75, Hardness 1.0, Elements {'Mg': 19.23, 'Si': 29.62, 'H': 0.53, 'O': 50.62}
			Density 2.75, Hardness 1.0, Elements {'Mg': 19.23, 'Si': 29.62, 'H': 0.53, 'O': 50.62}
			Density 2.75, Hardness 1.0, Elements {'Mg': 19.23, 'Si': 29.62, 'H': 0.53, 'O': 50.62}
			Density 2.75, Hardness 1.0, Elements {'Mg': 19.23, 'Si': 29.62, 'H': 0.53, 'O': 50.62}
	Found duplicates of "Khaidarkanite", with these properties :
			Density 2.84, Hardness 2.5, Elements {'Na': 1.16, 'Al': 12.01, 'Cu': 37.71, 'H': 2.69, 'O': 37.98, 'F': 8.46}
			Density 2.84, Hardness 2.5, Elements {'Na': 1.16, 'Al': 12.01, 'Cu': 37.71, 'H': 2.69, 'O': 37.98, 'F': 8.46}
	Found duplicates of "Khmaralite", with these properties :
			Density None, Hardness 7.0, Elements {'Mg': 16.64, 'Be': 1.03, 'Al': 23.09, 'Fe': 6.37, 'Si': 7.21, 'O': 45.65}
			Density None, Hardness 7.0, Elements {'Mg': 16.64, 'Be': 1.03, 'Al': 23.09, 'Fe': 6.37, 'Si': 7.21, 'O': 45.65}
	Found duplicates of "Khomyakovite", with these properties :
			Density None, Hardness None, Elements {'Na': 8.24, 'Sr': 7.85, 'Ca': 7.18, 'Zr': 8.17, 'Fe': 5.0, 'Si': 20.96, 'H': 0.07, 'W': 5.49, 'Cl': 0.53, 'O': 36.52}
			Density None, Hardness None, Elements {'Na': 8.24, 'Sr': 7.85, 'Ca': 7.18, 'Zr': 8.17, 'Fe': 5.0, 'Si': 20.96, 'H': 0.07, 'W': 5.49, 'Cl': 0.53, 'O': 36.52}
	Found duplicates of "Khristovite-Ce", with these properties :
			Density 4.08, Hardness 5.0, Elements {'Ca': 5.25, 'Ce': 11.47, 'RE': 16.5, 'Mg': 1.59, 'Ti': 0.78, 'Mn': 8.99, 'Al': 4.86, 'V': 0.42, 'Cr': 0.85, 'Fe': 0.91, 'Si': 13.79, 'H': 0.16, 'O': 32.08, 'F': 2.33}
			Density None, Hardness None, Elements {'Ca': 5.25, 'Ce': 11.47, 'RE': 16.5, 'Mg': 1.59, 'Ti': 0.78, 'Mn': 8.99, 'Al': 4.86, 'V': 0.42, 'Cr': 0.85, 'Fe': 0.91, 'Si': 13.79, 'H': 0.16, 'O': 32.08, 'F': 2.33}
	Found duplicates of "Kiddcreekite", with these properties :
			Density None, Hardness 4.0, Elements {'Cu': 40.55, 'Sn': 12.62, 'W': 19.55, 'S': 27.28}
			Density None, Hardness 4.0, Elements {'Cu': 40.55, 'Sn': 12.62, 'W': 19.55, 'S': 27.28}
	Found duplicates of "Geocronite", with these properties :
			Density 6.4, Hardness 2.75, Elements {'Sb': 14.08, 'As': 1.73, 'Pb': 67.12, 'S': 17.06}
			Density 6.4, Hardness 2.75, Elements {'Sb': 14.08, 'As': 1.73, 'Pb': 67.12, 'S': 17.06}
	Found duplicates of "Endellite", with these properties :
			Density 2.24, Hardness 1.5, Elements {'Al': 18.34, 'Si': 19.09, 'H': 2.74, 'O': 59.82}
			Density 2.24, Hardness 1.5, Elements {'Al': 18.34, 'Si': 19.09, 'H': 2.74, 'O': 59.82}
			Density 2.24, Hardness 1.5, Elements {'Al': 18.34, 'Si': 19.09, 'H': 2.74, 'O': 59.82}
	Found duplicates of "Kingstonite", with these properties :
			Density None, Hardness 6.0, Elements {'Ir': 16.48, 'Pt': 11.28, 'Rh': 46.59, 'S': 25.64}
			Density None, Hardness 6.0, Elements {'Ir': 16.48, 'Pt': 11.28, 'Rh': 46.59, 'S': 25.64}
	Found duplicates of "Kintoreite", with these properties :
			Density 4.29, Hardness 4.0, Elements {'Fe': 25.07, 'P': 9.27, 'H': 1.13, 'Pb': 31.01, 'O': 33.52}
			Density 4.29, Hardness 4.0, Elements {'Fe': 25.07, 'P': 9.27, 'H': 1.13, 'Pb': 31.01, 'O': 33.52}
	Found duplicates of "Kyrgyzstanite", with these properties :
			Density None, Hardness None, Elements {'Al': 20.46, 'Zn': 12.4, 'H': 3.44, 'S': 6.08, 'O': 57.63}
			Density None, Hardness None, Elements {'Al': 20.46, 'Zn': 12.4, 'H': 3.44, 'S': 6.08, 'O': 57.63}
			Density None, Hardness None, Elements {'Al': 20.46, 'Zn': 12.4, 'H': 3.44, 'S': 6.08, 'O': 57.63}
	Found duplicates of "Bassetite", with these properties :
			Density 3.63, Hardness 2.5, Elements {'U': 51.19, 'Fe': 6.01, 'P': 6.66, 'H': 1.73, 'O': 34.41}
			Density 3.63, Hardness 2.5, Elements {'U': 51.19, 'Fe': 6.01, 'P': 6.66, 'H': 1.73, 'O': 34.41}
	Found duplicates of "Kladnoite", with these properties :
			Density 1.47, Hardness None, Elements {'H': 3.43, 'C': 65.31, 'N': 9.52, 'O': 21.75}
			Density 1.47, Hardness None, Elements {'H': 3.43, 'C': 65.31, 'N': 9.52, 'O': 21.75}
	Found duplicates of "Emplectite", with these properties :
			Density 6.4, Hardness 2.0, Elements {'Cu': 18.88, 'Bi': 62.07, 'S': 19.05}
			Density 6.4, Hardness 2.0, Elements {'Cu': 18.88, 'Bi': 62.07, 'S': 19.05}
	Found duplicates of "Pseudorutile", with these properties :
			Density 3.8, Hardness 3.5, Elements {'Ti': 35.97, 'Fe': 27.97, 'O': 36.06}
			Density 3.8, Hardness 3.5, Elements {'Ti': 35.97, 'Fe': 27.97, 'O': 36.06}
			Density 3.8, Hardness 3.5, Elements {'Ti': 35.97, 'Fe': 27.97, 'O': 36.06}
	Found duplicates of "Kleberite", with these properties :
			Density 3.27, Hardness 4.25, Elements {'Ti': 46.1, 'Fe': 8.96, 'H': 1.29, 'O': 43.65}
			Density 3.27, Hardness 4.25, Elements {'Ti': 46.1, 'Fe': 8.96, 'H': 1.29, 'O': 43.65}
	Found duplicates of "Boehmite", with these properties :
			Density 3.03, Hardness 3.0, Elements {'Al': 44.98, 'H': 1.68, 'O': 53.34}
			Density 3.03, Hardness 3.0, Elements {'Al': 44.98, 'H': 1.68, 'O': 53.34}
			Density 3.03, Hardness 3.0, Elements {'Al': 44.98, 'H': 1.68, 'O': 53.34}
			Density 3.03, Hardness 3.0, Elements {'Al': 44.98, 'H': 1.68, 'O': 53.34}
			Density 3.03, Hardness 3.0, Elements {'Al': 44.98, 'H': 1.68, 'O': 53.34}
	Found duplicates of "Klyuchevskite-Duplicate", with these properties :
			Density 3.07, Hardness 3.5, Elements {'K': 15.04, 'Fe': 7.16, 'Cu': 24.44, 'S': 16.44, 'O': 36.92}
			Density 3.07, Hardness 3.5, Elements {'K': 15.04, 'Fe': 7.16, 'Cu': 24.44, 'S': 16.44, 'O': 36.92}
	Found duplicates of "Knasibfite", with these properties :
			Density 2.75, Hardness None, Elements {'K': 16.69, 'Na': 13.06, 'Si': 11.29, 'B': 1.49, 'F': 57.46}
			Density 2.75, Hardness None, Elements {'K': 16.69, 'Na': 13.06, 'Si': 11.29, 'B': 1.49, 'F': 57.46}
	Found duplicates of "Fayalite", with these properties :
			Density 4.39, Hardness 6.5, Elements {'Fe': 54.81, 'Si': 13.78, 'O': 31.41}
			Density 4.39, Hardness 6.5, Elements {'Fe': 54.81, 'Si': 13.78, 'O': 31.41}
			Density 4.39, Hardness 6.5, Elements {'Fe': 54.81, 'Si': 13.78, 'O': 31.41}
	Found duplicates of "Kochite", with these properties :
			Density 3.32, Hardness 5.0, Elements {'Na': 7.9, 'Sr': 0.12, 'Ca': 15.74, 'La': 0.18, 'Ce': 0.47, 'Y': 0.3, 'Hf': 0.12, 'Zr': 9.21, 'Ti': 5.22, 'Mn': 3.91, 'Nb': 1.36, 'Al': 0.04, 'V': 0.03, 'Fe': 0.85, 'Si': 15.19, 'O': 32.35, 'F': 7.03}
			Density 3.32, Hardness 5.0, Elements {'Na': 7.9, 'Sr': 0.12, 'Ca': 15.74, 'La': 0.18, 'Ce': 0.47, 'Y': 0.3, 'Hf': 0.12, 'Zr': 9.21, 'Ti': 5.22, 'Mn': 3.91, 'Nb': 1.36, 'Al': 0.04, 'V': 0.03, 'Fe': 0.85, 'Si': 15.19, 'O': 32.35, 'F': 7.03}
	Found duplicates of "Kochsandorite", with these properties :
			Density 2.486, Hardness 2.25, Elements {'Ca': 12.21, 'Al': 18.26, 'H': 2.25, 'C': 7.72, 'O': 59.56}
			Density 2.486, Hardness 2.25, Elements {'Ca': 12.21, 'Al': 18.26, 'H': 2.25, 'C': 7.72, 'O': 59.56}
	Found duplicates of "Kottigite", with these properties :
			Density 3.33, Hardness 2.75, Elements {'Zn': 31.74, 'As': 24.24, 'H': 2.61, 'O': 41.41}
			Density 3.33, Hardness 2.75, Elements {'Zn': 31.74, 'As': 24.24, 'H': 2.61, 'O': 41.41}
	Found duplicates of "Florencite-Ce", with these properties :
			Density 3.58, Hardness 5.5, Elements {'Ce': 27.31, 'Al': 15.78, 'P': 12.07, 'H': 1.18, 'O': 43.66}
			Density 3.58, Hardness 5.5, Elements {'Ce': 27.31, 'Al': 15.78, 'P': 12.07, 'H': 1.18, 'O': 43.66}
	Found duplicates of "Kokchetavite", with these properties :
			Density None, Hardness None, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
			Density None, Hardness None, Elements {'K': 14.05, 'Al': 9.69, 'Si': 30.27, 'O': 45.99}
	Found duplicates of "Kolbeckite", with these properties :
			Density 2.44, Hardness 5.0, Elements {'Sc': 23.99, 'Al': 0.15, 'V': 0.87, 'Fe': 0.63, 'P': 17.58, 'H': 2.29, 'O': 54.49}
			Density 2.44, Hardness 5.0, Elements {'Sc': 23.99, 'Al': 0.15, 'V': 0.87, 'Fe': 0.63, 'P': 17.58, 'H': 2.29, 'O': 54.49}
			Density 2.44, Hardness 5.0, Elements {'Sc': 23.99, 'Al': 0.15, 'V': 0.87, 'Fe': 0.63, 'P': 17.58, 'H': 2.29, 'O': 54.49}
	Found duplicates of "Komarovite", with these properties :
			Density 3.68, Hardness 4.0, Elements {'Na': 0.91, 'Ca': 3.19, 'Ti': 1.9, 'Mn': 4.37, 'Nb': 33.26, 'Si': 11.17, 'H': 1.4, 'O': 42.65, 'F': 1.13}
			Density 3.68, Hardness 4.0, Elements {'Na': 0.91, 'Ca': 3.19, 'Ti': 1.9, 'Mn': 4.37, 'Nb': 33.26, 'Si': 11.17, 'H': 1.4, 'O': 42.65, 'F': 1.13}
	Found duplicates of "Komkovite", with these properties :
			Density 3.31, Hardness 3.5, Elements {'Ba': 26.88, 'Zr': 17.86, 'Si': 16.49, 'H': 1.18, 'O': 37.58}
			Density 3.31, Hardness 3.5, Elements {'Ba': 26.88, 'Zr': 17.86, 'Si': 16.49, 'H': 1.18, 'O': 37.58}
	Found duplicates of "Konderite", with these properties :
			Density None, Hardness 5.5, Elements {'Cu': 9.49, 'Ir': 9.57, 'Pt': 19.43, 'Rh': 25.63, 'Pb': 10.32, 'S': 25.55}
			Density None, Hardness 5.5, Elements {'Cu': 9.49, 'Ir': 9.57, 'Pt': 19.43, 'Rh': 25.63, 'Pb': 10.32, 'S': 25.55}
	Found duplicates of "Amalgam", with these properties :
			Density 13.92, Hardness 3.25, Elements {'Ag': 26.39, 'Hg': 73.61}
			Density 13.92, Hardness 3.25, Elements {'Ag': 26.39, 'Hg': 73.61}
	Found duplicates of "Koragoite", with these properties :
			Density 5.46, Hardness 4.5, Elements {'Ta': 16.03, 'Ti': 1.16, 'Mn': 13.5, 'Nb': 17.96, 'Fe': 3.37, 'W': 22.21, 'O': 25.77}
			Density 5.46, Hardness 4.5, Elements {'Ta': 16.03, 'Ti': 1.16, 'Mn': 13.5, 'Nb': 17.96, 'Fe': 3.37, 'W': 22.21, 'O': 25.77}
	Found duplicates of "Kornite", with these properties :
			Density None, Hardness 6.5, Elements {'Na': 4.82, 'Ca': 4.2, 'Al': 4.95, 'Fe': 24.9, 'Si': 20.62, 'H': 0.21, 'O': 40.28}
			Density None, Hardness 6.5, Elements {'Na': 4.82, 'Ca': 4.2, 'Al': 4.95, 'Fe': 24.9, 'Si': 20.62, 'H': 0.21, 'O': 40.28}
	Found duplicates of "Kosmochlor", with these properties :
			Density 3.6, Hardness 6.5, Elements {'Na': 10.12, 'Cr': 22.89, 'Si': 24.73, 'O': 42.26}
			Density 3.6, Hardness 6.5, Elements {'Na': 10.12, 'Cr': 22.89, 'Si': 24.73, 'O': 42.26}
			Density 3.6, Hardness 6.5, Elements {'Na': 10.12, 'Cr': 22.89, 'Si': 24.73, 'O': 42.26}
	Found duplicates of "Kosnarite", with these properties :
			Density 3.194, Hardness 4.5, Elements {'K': 7.72, 'Zr': 36.02, 'P': 18.35, 'O': 37.91}
			Density 3.194, Hardness 4.5, Elements {'K': 7.72, 'Zr': 36.02, 'P': 18.35, 'O': 37.91}
	Found duplicates of "Kozoite-La", with these properties :
			Density None, Hardness None, Elements {'Sr': 0.92, 'Ca': 4.41, 'La': 29.82, 'Pr': 3.69, 'Sm': 0.79, 'Gd': 1.65, 'Y': 3.26, 'H': 0.65, 'C': 6.29, 'Nd': 15.1, 'O': 33.43}
			Density None, Hardness None, Elements {'Sr': 0.92, 'Ca': 4.41, 'La': 29.82, 'Pr': 3.69, 'Sm': 0.79, 'Gd': 1.65, 'Y': 3.26, 'H': 0.65, 'C': 6.29, 'Nd': 15.1, 'O': 33.43}
	Found duplicates of "Kozoite-Nd", with these properties :
			Density None, Hardness None, Elements {'La': 20.31, 'Pr': 6.87, 'Sm': 3.66, 'H': 0.49, 'C': 5.85, 'Nd': 31.63, 'O': 31.19}
			Density None, Hardness None, Elements {'La': 20.31, 'Pr': 6.87, 'Sm': 3.66, 'H': 0.49, 'C': 5.85, 'Nd': 31.63, 'O': 31.19}
	Found duplicates of "Krasnovite", with these properties :
			Density 3.7, Hardness 2.0, Elements {'Ba': 45.49, 'Mg': 2.01, 'Al': 6.7, 'P': 7.69, 'H': 1.34, 'C': 0.99, 'O': 35.77}
			Density 3.7, Hardness 2.0, Elements {'Ba': 45.49, 'Mg': 2.01, 'Al': 6.7, 'P': 7.69, 'H': 1.34, 'C': 0.99, 'O': 35.77}
	Found duplicates of "Krettnichite", with these properties :
			Density None, Hardness 4.5, Elements {'Sr': 1.58, 'Mn': 16.88, 'V': 17.49, 'Fe': 1.01, 'Co': 2.13, 'As': 1.35, 'H': 0.4, 'Pb': 29.95, 'O': 29.2}
			Density None, Hardness 4.5, Elements {'Sr': 1.58, 'Mn': 16.88, 'V': 17.49, 'Fe': 1.01, 'Co': 2.13, 'As': 1.35, 'H': 0.4, 'Pb': 29.95, 'O': 29.2}
	Found duplicates of "Krieselite", with these properties :
			Density None, Hardness None, Elements {'Al': 17.54, 'Ga': 15.1, 'Ge': 23.59, 'H': 0.87, 'C': 1.3, 'O': 41.59}
			Density None, Hardness None, Elements {'Al': 17.54, 'Ga': 15.1, 'Ge': 23.59, 'H': 0.87, 'C': 1.3, 'O': 41.59}
	Found duplicates of "Kristiansenite", with these properties :
			Density 3.3, Hardness 5.75, Elements {'Na': 0.32, 'Ca': 13.21, 'Zr': 0.31, 'Sc': 5.25, 'Al': 0.19, 'Fe': 1.34, 'Si': 19.09, 'Sn': 21.6, 'H': 0.23, 'O': 38.46}
			Density 3.3, Hardness 5.75, Elements {'Na': 0.32, 'Ca': 13.21, 'Zr': 0.31, 'Sc': 5.25, 'Al': 0.19, 'Fe': 1.34, 'Si': 19.09, 'Sn': 21.6, 'H': 0.23, 'O': 38.46}
	Found duplicates of "Krivovichevite", with these properties :
			Density None, Hardness 3.0, Elements {'Al': 2.91, 'H': 0.82, 'Pb': 72.2, 'S': 3.79, 'O': 20.28}
			Density None, Hardness 3.0, Elements {'Al': 2.91, 'H': 0.82, 'Pb': 72.2, 'S': 3.79, 'O': 20.28}
	Found duplicates of "Krohnkite", with these properties :
			Density 2.48, Hardness 2.75, Elements {'Na': 13.62, 'Cu': 18.82, 'H': 1.19, 'S': 18.99, 'O': 47.38}
			Density 2.48, Hardness 2.75, Elements {'Na': 13.62, 'Cu': 18.82, 'H': 1.19, 'S': 18.99, 'O': 47.38}
	Found duplicates of "Kuannersuite-Ce", with these properties :
			Density None, Hardness 5.0, Elements {'Ba': 45.2, 'Na': 2.89, 'Sr': 0.77, 'La': 2.04, 'Ce': 8.22, 'Sm': 0.44, 'Th': 0.27, 'Si': 0.23, 'P': 10.94, 'Nd': 3.64, 'Cl': 1.21, 'O': 22.26, 'F': 1.89}
			Density None, Hardness 5.0, Elements {'Ba': 45.2, 'Na': 2.89, 'Sr': 0.77, 'La': 2.04, 'Ce': 8.22, 'Sm': 0.44, 'Th': 0.27, 'Si': 0.23, 'P': 10.94, 'Nd': 3.64, 'Cl': 1.21, 'O': 22.26, 'F': 1.89}
	Found duplicates of "Kudriavite", with these properties :
			Density None, Hardness None, Elements {'Mn': 0.24, 'Cd': 8.3, 'In': 2.83, 'Fe': 0.16, 'Bi': 55.39, 'Pb': 13.21, 'Se': 2.17, 'S': 17.7}
			Density None, Hardness None, Elements {'Mn': 0.24, 'Cd': 8.3, 'In': 2.83, 'Fe': 0.16, 'Bi': 55.39, 'Pb': 13.21, 'Se': 2.17, 'S': 17.7}
	Found duplicates of "Kukharenkoite-Ce", with these properties :
			Density 4.65, Hardness 4.5, Elements {'Ba': 44.75, 'Ce': 22.83, 'C': 5.87, 'O': 23.46, 'F': 3.1}
			Density None, Hardness None, Elements {'Ba': 44.75, 'Ce': 22.83, 'C': 5.87, 'O': 23.46, 'F': 3.1}
	Found duplicates of "Kukharenkoite-La", with these properties :
			Density None, Hardness 4.0, Elements {'K': 0.26, 'Ba': 39.88, 'Na': 0.19, 'Sr': 2.02, 'Ca': 0.73, 'La': 9.63, 'Ce': 4.62, 'Pr': 0.7, 'Th': 8.42, 'C': 5.89, 'Nd': 0.71, 'O': 23.52, 'F': 3.42}
			Density None, Hardness 4.0, Elements {'K': 0.26, 'Ba': 39.88, 'Na': 0.19, 'Sr': 2.02, 'Ca': 0.73, 'La': 9.63, 'Ce': 4.62, 'Pr': 0.7, 'Th': 8.42, 'C': 5.89, 'Nd': 0.71, 'O': 23.52, 'F': 3.42}
	Found duplicates of "Kukisvumite", with these properties :
			Density 2.9, Hardness 5.75, Elements {'Na': 12.1, 'Ti': 16.81, 'Zn': 5.74, 'Si': 19.72, 'H': 0.71, 'O': 44.93}
			Density 2.9, Hardness 5.75, Elements {'Na': 12.1, 'Ti': 16.81, 'Zn': 5.74, 'Si': 19.72, 'H': 0.71, 'O': 44.93}
	Found duplicates of "Kuksite", with these properties :
			Density None, Hardness 5.0, Elements {'Zn': 15.93, 'Te': 10.36, 'P': 5.03, 'Pb': 50.48, 'O': 18.19}
			Density None, Hardness 5.0, Elements {'Zn': 15.93, 'Te': 10.36, 'P': 5.03, 'Pb': 50.48, 'O': 18.19}
	Found duplicates of "Kulanite", with these properties :
			Density 3.91, Hardness 4.0, Elements {'Ba': 21.54, 'Mg': 0.76, 'Mn': 5.17, 'Al': 8.46, 'Fe': 11.39, 'P': 14.57, 'H': 0.47, 'O': 37.64}
			Density 3.91, Hardness 4.0, Elements {'Ba': 21.54, 'Mg': 0.76, 'Mn': 5.17, 'Al': 8.46, 'Fe': 11.39, 'P': 14.57, 'H': 0.47, 'O': 37.64}
	Found duplicates of "Spodumene", with these properties :
			Density None, Hardness None, Elements {'Li': 3.73, 'Al': 14.5, 'Si': 30.18, 'O': 51.59}
			Density None, Hardness None, Elements {'Li': 3.73, 'Al': 14.5, 'Si': 30.18, 'O': 51.59}
			Density None, Hardness None, Elements {'Li': 3.73, 'Al': 14.5, 'Si': 30.18, 'O': 51.59}
			Density None, Hardness None, Elements {'Li': 3.73, 'Al': 14.5, 'Si': 30.18, 'O': 51.59}
	Found duplicates of "Kupcikite", with these properties :
			Density None, Hardness 3.5, Elements {'Cd': 0.14, 'Fe': 2.07, 'Cu': 13.18, 'Ag': 0.07, 'Bi': 64.44, 'Sb': 0.08, 'Pb': 0.13, 'S': 19.89}
			Density None, Hardness 3.5, Elements {'Cd': 0.14, 'Fe': 2.07, 'Cu': 13.18, 'Ag': 0.07, 'Bi': 64.44, 'Sb': 0.08, 'Pb': 0.13, 'S': 19.89}
	Found duplicates of "Copper", with these properties :
			Density 8.94, Hardness 2.75, Elements {'Cu': 100.0}
			Density 8.94, Hardness 2.75, Elements {'Cu': 100.0}
			Density 8.94, Hardness 2.75, Elements {'Cu': 100.0}
	Found duplicates of "Kupletskite-Cs", with these properties :
			Density 3.68, Hardness 4.0, Elements {'Cs': 13.69, 'K': 1.34, 'Na': 1.58, 'Li': 0.19, 'Ti': 4.6, 'Mn': 17.36, 'Nb': 3.19, 'Fe': 8.06, 'Si': 15.43, 'H': 0.28, 'O': 32.97, 'F': 1.3}
			Density 3.68, Hardness 4.0, Elements {'Cs': 13.69, 'K': 1.34, 'Na': 1.58, 'Li': 0.19, 'Ti': 4.6, 'Mn': 17.36, 'Nb': 3.19, 'Fe': 8.06, 'Si': 15.43, 'H': 0.28, 'O': 32.97, 'F': 1.3}
			Density 3.68, Hardness 4.0, Elements {'Cs': 13.69, 'K': 1.34, 'Na': 1.58, 'Li': 0.19, 'Ti': 4.6, 'Mn': 17.36, 'Nb': 3.19, 'Fe': 8.06, 'Si': 15.43, 'H': 0.28, 'O': 32.97, 'F': 1.3}
	Found duplicates of "Kurgantaite", with these properties :
			Density 2.99, Hardness 6.25, Elements {'Sr': 22.68, 'Ca': 10.69, 'B': 14.27, 'H': 0.53, 'Cl': 9.74, 'O': 42.09}
			Density 2.99, Hardness 6.25, Elements {'Sr': 22.68, 'Ca': 10.69, 'B': 14.27, 'H': 0.53, 'Cl': 9.74, 'O': 42.09}
			Density 2.99, Hardness 6.25, Elements {'Sr': 22.68, 'Ca': 10.69, 'B': 14.27, 'H': 0.53, 'Cl': 9.74, 'O': 42.09}
	Found duplicates of "Harmotome", with these properties :
			Density 2.46, Hardness 4.5, Elements {'K': 0.55, 'Ba': 15.58, 'Na': 0.65, 'Al': 7.66, 'Si': 23.91, 'H': 1.72, 'O': 49.93}
			Density 2.46, Hardness 4.5, Elements {'K': 0.55, 'Ba': 15.58, 'Na': 0.65, 'Al': 7.66, 'Si': 23.91, 'H': 1.72, 'O': 49.93}
			Density 2.46, Hardness 4.5, Elements {'K': 0.55, 'Ba': 15.58, 'Na': 0.65, 'Al': 7.66, 'Si': 23.91, 'H': 1.72, 'O': 49.93}
			Density 2.46, Hardness 4.5, Elements {'K': 0.55, 'Ba': 15.58, 'Na': 0.65, 'Al': 7.66, 'Si': 23.91, 'H': 1.72, 'O': 49.93}
			Density 2.46, Hardness 4.5, Elements {'K': 0.55, 'Ba': 15.58, 'Na': 0.65, 'Al': 7.66, 'Si': 23.91, 'H': 1.72, 'O': 49.93}
			Density 2.46, Hardness 4.5, Elements {'K': 0.55, 'Ba': 15.58, 'Na': 0.65, 'Al': 7.66, 'Si': 23.91, 'H': 1.72, 'O': 49.93}
	Found duplicates of "Kusachiite", with these properties :
			Density 8.5, Hardness 4.5, Elements {'Cu': 11.65, 'Bi': 76.62, 'O': 11.73}
			Density 8.5, Hardness 4.5, Elements {'Cu': 11.65, 'Bi': 76.62, 'O': 11.73}
	Found duplicates of "Wakefieldite-Ce", with these properties :
			Density 4.76, Hardness 4.5, Elements {'Ce': 29.82, 'V': 18.07, 'Pb': 29.4, 'O': 22.7}
			Density 4.76, Hardness 4.5, Elements {'Ce': 29.82, 'V': 18.07, 'Pb': 29.4, 'O': 22.7}
			Density 4.76, Hardness 4.5, Elements {'Ce': 29.82, 'V': 18.07, 'Pb': 29.4, 'O': 22.7}
	Found duplicates of "Kutnohorite", with these properties :
			Density 3.11, Hardness 3.75, Elements {'Ca': 19.46, 'Mg': 3.54, 'Mn': 16.01, 'Fe': 2.71, 'C': 11.66, 'O': 46.61}
			Density 3.11, Hardness 3.75, Elements {'Ca': 19.46, 'Mg': 3.54, 'Mn': 16.01, 'Fe': 2.71, 'C': 11.66, 'O': 46.61}
	Found duplicates of "Kuzelite", with these properties :
			Density 2.0, Hardness 1.75, Elements {'Ca': 25.75, 'Al': 8.67, 'H': 3.89, 'S': 5.15, 'O': 56.54}
			Density 2.0, Hardness 1.75, Elements {'Ca': 25.75, 'Al': 8.67, 'H': 3.89, 'S': 5.15, 'O': 56.54}
	Found duplicates of "Kuzmenkoite-Mn", with these properties :
			Density 2.82, Hardness 5.0, Elements {'K': 6.07, 'Na': 0.63, 'Ti': 15.74, 'Mn': 3.51, 'Nb': 3.39, 'Fe': 1.02, 'Si': 20.52, 'H': 1.24, 'O': 47.88}
			Density 2.82, Hardness 5.0, Elements {'K': 6.07, 'Na': 0.63, 'Ti': 15.74, 'Mn': 3.51, 'Nb': 3.39, 'Fe': 1.02, 'Si': 20.52, 'H': 1.24, 'O': 47.88}
			Density 2.82, Hardness 5.0, Elements {'K': 6.07, 'Na': 0.63, 'Ti': 15.74, 'Mn': 3.51, 'Nb': 3.39, 'Fe': 1.02, 'Si': 20.52, 'H': 1.24, 'O': 47.88}
	Found duplicates of "Kuzmenkoite-Zn", with these properties :
			Density 2.82, Hardness None, Elements {'K': 4.76, 'Ba': 1.81, 'Na': 0.36, 'Sr': 0.14, 'Ca': 1.58, 'Mg': 0.02, 'Ti': 10.68, 'Mn': 1.63, 'Nb': 8.87, 'Al': 0.04, 'Zn': 3.01, 'Fe': 0.41, 'Si': 18.22, 'H': 1.56, 'O': 46.89}
			Density 2.82, Hardness None, Elements {'K': 4.76, 'Ba': 1.81, 'Na': 0.36, 'Sr': 0.14, 'Ca': 1.58, 'Mg': 0.02, 'Ti': 10.68, 'Mn': 1.63, 'Nb': 8.87, 'Al': 0.04, 'Zn': 3.01, 'Fe': 0.41, 'Si': 18.22, 'H': 1.56, 'O': 46.89}
	Found duplicates of "Kyanite", with these properties :
			Density 3.61, Hardness 5.5, Elements {'Al': 33.3, 'Si': 17.33, 'O': 49.37}
			Density 3.61, Hardness 5.5, Elements {'Al': 33.3, 'Si': 17.33, 'O': 49.37}
	Found duplicates of "Labradorite", with these properties :
			Density 2.69, Hardness 7.0, Elements {'Na': 3.38, 'Ca': 8.85, 'Al': 15.88, 'Si': 24.8, 'O': 47.09}
			Density 2.69, Hardness 7.0, Elements {'Na': 3.38, 'Ca': 8.85, 'Al': 15.88, 'Si': 24.8, 'O': 47.09}
	Found duplicates of "Labuntsovite-Fe", with these properties :
			Density 2.94, Hardness 5.0, Elements {'K': 6.95, 'Ba': 7.38, 'Na': 4.09, 'Mg': 0.2, 'Ti': 15.44, 'Mn': 0.23, 'Nb': 0.77, 'Fe': 1.85, 'Si': 18.58, 'H': 0.93, 'O': 43.59}
			Density 2.94, Hardness 5.0, Elements {'K': 6.95, 'Ba': 7.38, 'Na': 4.09, 'Mg': 0.2, 'Ti': 15.44, 'Mn': 0.23, 'Nb': 0.77, 'Fe': 1.85, 'Si': 18.58, 'H': 0.93, 'O': 43.59}
	Found duplicates of "Labuntsovite-Mg", with these properties :
			Density 2.88, Hardness 5.0, Elements {'K': 7.21, 'Ba': 5.76, 'Na': 3.47, 'Mg': 0.71, 'Ti': 14.45, 'Nb': 2.73, 'Al': 0.11, 'Fe': 1.17, 'Si': 18.72, 'H': 1.04, 'O': 44.61}
			Density 2.88, Hardness 5.0, Elements {'K': 7.21, 'Ba': 5.76, 'Na': 3.47, 'Mg': 0.71, 'Ti': 14.45, 'Nb': 2.73, 'Al': 0.11, 'Fe': 1.17, 'Si': 18.72, 'H': 1.04, 'O': 44.61}
	Found duplicates of "Labuntsovite-Mn", with these properties :
			Density 2.95, Hardness 6.0, Elements {'K': 5.96, 'Ba': 8.0, 'Na': 2.37, 'Ca': 0.9, 'Mg': 0.22, 'Ti': 15.44, 'Mn': 1.72, 'Nb': 0.83, 'Al': 0.73, 'Fe': 1.0, 'Si': 18.37, 'H': 0.87, 'O': 43.59}
			Density 2.95, Hardness 6.0, Elements {'K': 5.96, 'Ba': 8.0, 'Na': 2.37, 'Ca': 0.9, 'Mg': 0.22, 'Ti': 15.44, 'Mn': 1.72, 'Nb': 0.83, 'Al': 0.73, 'Fe': 1.0, 'Si': 18.37, 'H': 0.87, 'O': 43.59}
			Density 2.95, Hardness 6.0, Elements {'K': 5.96, 'Ba': 8.0, 'Na': 2.37, 'Ca': 0.9, 'Mg': 0.22, 'Ti': 15.44, 'Mn': 1.72, 'Nb': 0.83, 'Al': 0.73, 'Fe': 1.0, 'Si': 18.37, 'H': 0.87, 'O': 43.59}
	Found duplicates of "Labyrinthite", with these properties :
			Density 2.88, Hardness None, Elements {'K': 0.93, 'Na': 12.53, 'Sr': 1.06, 'Ca': 7.72, 'Ce': 0.23, 'Zr': 8.87, 'Ti': 0.41, 'Mn': 0.78, 'Fe': 2.0, 'Si': 23.57, 'H': 0.18, 'Cl': 1.71, 'O': 39.89, 'F': 0.11}
			Density 2.88, Hardness None, Elements {'K': 0.93, 'Na': 12.53, 'Sr': 1.06, 'Ca': 7.72, 'Ce': 0.23, 'Zr': 8.87, 'Ti': 0.41, 'Mn': 0.78, 'Fe': 2.0, 'Si': 23.57, 'H': 0.18, 'Cl': 1.71, 'O': 39.89, 'F': 0.11}
	Found duplicates of "Laflammeite", with these properties :
			Density None, Hardness 3.5, Elements {'Pd': 40.02, 'Pb': 51.94, 'S': 8.04}
			Density None, Hardness 3.5, Elements {'Pd': 40.02, 'Pb': 51.94, 'S': 8.04}
	Found duplicates of "Laforetite", with these properties :
			Density None, Hardness 3.0, Elements {'In': 40.03, 'Ag': 37.61, 'S': 22.36}
			Density None, Hardness 3.0, Elements {'In': 40.03, 'Ag': 37.61, 'S': 22.36}
	Found duplicates of "Lafossaite", with these properties :
			Density None, Hardness 3.5, Elements {'Tl': 82.99, 'Br': 6.07, 'Cl': 10.95}
			Density None, Hardness 3.5, Elements {'Tl': 82.99, 'Br': 6.07, 'Cl': 10.95}
	Found duplicates of "Lakargiite", with these properties :
			Density None, Hardness 8.25, Elements {'Sr': 0.1, 'Ca': 22.19, 'La': 0.47, 'Ce': 0.47, 'Y': 0.1, 'Hf': 0.8, 'Th': 0.78, 'Mg': 0.01, 'Zr': 29.85, 'Sc': 0.25, 'U': 1.87, 'Ta': 0.1, 'Ti': 4.74, 'Nb': 0.37, 'Al': 0.02, 'V': 0.09, 'Cr': 0.23, 'Fe': 1.73, 'Si': 0.02, 'Sn': 8.74, 'Nd': 0.08, 'O': 26.99}
			Density None, Hardness 8.25, Elements {'Sr': 0.1, 'Ca': 22.19, 'La': 0.47, 'Ce': 0.47, 'Y': 0.1, 'Hf': 0.8, 'Th': 0.78, 'Mg': 0.01, 'Zr': 29.85, 'Sc': 0.25, 'U': 1.87, 'Ta': 0.1, 'Ti': 4.74, 'Nb': 0.37, 'Al': 0.02, 'V': 0.09, 'Cr': 0.23, 'Fe': 1.73, 'Si': 0.02, 'Sn': 8.74, 'Nd': 0.08, 'O': 26.99}
	Found duplicates of "Lakebogaite", with these properties :
			Density 3.64, Hardness 3.0, Elements {'Na': 1.49, 'Sr': 0.71, 'Ca': 3.25, 'U': 34.79, 'Al': 0.66, 'Fe': 8.39, 'P': 10.24, 'H': 1.49, 'O': 38.97}
			Density 3.64, Hardness 3.0, Elements {'Na': 1.49, 'Sr': 0.71, 'Ca': 3.25, 'U': 34.79, 'Al': 0.66, 'Fe': 8.39, 'P': 10.24, 'H': 1.49, 'O': 38.97}
	Found duplicates of "Lalondeite", with these properties :
			Density 2.5, Hardness 3.0, Elements {'K': 0.6, 'Na': 8.5, 'Ca': 10.87, 'Si': 31.5, 'H': 0.49, 'Cl': 0.17, 'O': 46.57, 'F': 1.31}
			Density 2.5, Hardness 3.0, Elements {'K': 0.6, 'Na': 8.5, 'Ca': 10.87, 'Si': 31.5, 'H': 0.49, 'Cl': 0.17, 'O': 46.57, 'F': 1.31}
	Found duplicates of "Asbolane", with these properties :
			Density None, Hardness 6.0, Elements {'Ca': 2.24, 'Mn': 46.1, 'Co': 3.3, 'Ni': 9.85, 'H': 1.8, 'O': 36.7}
			Density None, Hardness 6.0, Elements {'Ca': 2.24, 'Mn': 46.1, 'Co': 3.3, 'Ni': 9.85, 'H': 1.8, 'O': 36.7}
			Density None, Hardness 6.0, Elements {'Ca': 2.24, 'Mn': 46.1, 'Co': 3.3, 'Ni': 9.85, 'H': 1.8, 'O': 36.7}
	Found duplicates of "Langisite", with these properties :
			Density 8.17, Hardness 6.25, Elements {'Co': 33.04, 'Ni': 10.97, 'As': 56.0}
			Density 8.17, Hardness 6.25, Elements {'Co': 33.04, 'Ni': 10.97, 'As': 56.0}
	Found duplicates of "Lanmuchangite", with these properties :
			Density 2.22, Hardness 3.25, Elements {'Al': 4.23, 'Tl': 32.04, 'H': 3.76, 'S': 10.05, 'O': 49.91}
			Density 2.22, Hardness 3.25, Elements {'Al': 4.23, 'Tl': 32.04, 'H': 3.76, 'S': 10.05, 'O': 49.91}
	Found duplicates of "Lapeyreite", with these properties :
			Density None, Hardness None, Elements {'Cu': 37.79, 'As': 29.7, 'H': 0.8, 'O': 31.71}
			Density None, Hardness None, Elements {'Cu': 37.79, 'As': 29.7, 'H': 0.8, 'O': 31.71}
	Found duplicates of "Lapieite", with these properties :
			Density 4.966, Hardness 4.75, Elements {'Cu': 18.68, 'Ni': 17.25, 'Sb': 35.79, 'S': 28.28}
			Density 4.966, Hardness 4.75, Elements {'Cu': 18.68, 'Ni': 17.25, 'Sb': 35.79, 'S': 28.28}
	Found duplicates of "Lazurite", with these properties :
			Density 2.4, Hardness 5.5, Elements {'Na': 13.84, 'Ca': 8.04, 'Al': 16.24, 'Si': 16.91, 'S': 6.43, 'O': 38.53}
			Density 2.4, Hardness 5.5, Elements {'Na': 13.84, 'Ca': 8.04, 'Al': 16.24, 'Si': 16.91, 'S': 6.43, 'O': 38.53}
			Density 2.4, Hardness 5.5, Elements {'Na': 13.84, 'Ca': 8.04, 'Al': 16.24, 'Si': 16.91, 'S': 6.43, 'O': 38.53}
			Density 2.4, Hardness 5.5, Elements {'Na': 13.84, 'Ca': 8.04, 'Al': 16.24, 'Si': 16.91, 'S': 6.43, 'O': 38.53}
			Density 2.4, Hardness 5.5, Elements {'Na': 13.84, 'Ca': 8.04, 'Al': 16.24, 'Si': 16.91, 'S': 6.43, 'O': 38.53}
	Found duplicates of "Rostite", with these properties :
			Density 1.892, Hardness None, Elements {'Al': 11.7, 'H': 4.72, 'S': 13.91, 'O': 68.02, 'F': 1.65}
			Density 1.892, Hardness None, Elements {'Al': 11.7, 'H': 4.72, 'S': 13.91, 'O': 68.02, 'F': 1.65}
	Found duplicates of "Larisaite", with these properties :
			Density None, Hardness 1.0, Elements {'K': 0.57, 'Na': 1.51, 'Ca': 0.16, 'U': 59.52, 'H': 0.85, 'Se': 12.78, 'O': 24.61}
			Density None, Hardness 1.0, Elements {'K': 0.57, 'Na': 1.51, 'Ca': 0.16, 'U': 59.52, 'H': 0.85, 'Se': 12.78, 'O': 24.61}
	Found duplicates of "Larosite", with these properties :
			Density None, Hardness 3.25, Elements {'Cu': 53.51, 'Ag': 9.56, 'Bi': 9.26, 'Pb': 9.18, 'S': 18.48}
			Density None, Hardness 3.25, Elements {'Cu': 53.51, 'Ag': 9.56, 'Bi': 9.26, 'Pb': 9.18, 'S': 18.48}
	Found duplicates of "Lasalite", with these properties :
			Density 2.38, Hardness 1.0, Elements {'K': 0.39, 'Na': 3.01, 'Ca': 1.26, 'Mg': 3.27, 'V': 34.64, 'H': 2.67, 'S': 1.03, 'O': 53.73}
			Density 2.38, Hardness 1.0, Elements {'K': 0.39, 'Na': 3.01, 'Ca': 1.26, 'Mg': 3.27, 'V': 34.64, 'H': 2.67, 'S': 1.03, 'O': 53.73}
	Found duplicates of "Latrappite", with these properties :
			Density 4.4, Hardness 5.5, Elements {'Na': 2.98, 'Ca': 20.78, 'Mg': 1.58, 'Ti': 6.21, 'Nb': 30.11, 'Fe': 7.24, 'O': 31.11}
			Density 4.4, Hardness 5.5, Elements {'Na': 2.98, 'Ca': 20.78, 'Mg': 1.58, 'Ti': 6.21, 'Nb': 30.11, 'Fe': 7.24, 'O': 31.11}
	Found duplicates of "Laumontite", with these properties :
			Density 2.29, Hardness 3.75, Elements {'Ca': 8.52, 'Al': 11.47, 'Si': 23.88, 'H': 1.71, 'O': 54.42}
			Density 2.29, Hardness 3.75, Elements {'Ca': 8.52, 'Al': 11.47, 'Si': 23.88, 'H': 1.71, 'O': 54.42}
			Density 2.29, Hardness 3.75, Elements {'Ca': 8.52, 'Al': 11.47, 'Si': 23.88, 'H': 1.71, 'O': 54.42}
			Density 2.29, Hardness 3.75, Elements {'Ca': 8.52, 'Al': 11.47, 'Si': 23.88, 'H': 1.71, 'O': 54.42}
	Found duplicates of "Launayite", with these properties :
			Density 5.75, Hardness 3.75, Elements {'Sb': 32.7, 'Pb': 47.09, 'S': 20.21}
			Density 5.75, Hardness 3.75, Elements {'Sb': 32.7, 'Pb': 47.09, 'S': 20.21}
	Found duplicates of "Lautenthalite", with these properties :
			Density 3.84, Hardness 2.5, Elements {'Cu': 31.4, 'H': 1.49, 'Pb': 25.59, 'S': 7.92, 'O': 33.6}
			Density 3.84, Hardness 2.5, Elements {'Cu': 31.4, 'H': 1.49, 'Pb': 25.59, 'S': 7.92, 'O': 33.6}
	Found duplicates of "Lead", with these properties :
			Density 11.37, Hardness 2.25, Elements {'Pb': 100.0}
			Density 11.37, Hardness 2.25, Elements {'Pb': 100.0}
	Found duplicates of "Leadamalgam", with these properties :
			Density None, Hardness 1.5, Elements {'Hg': 32.62, 'Pb': 67.38}
			Density None, Hardness 1.5, Elements {'Hg': 32.62, 'Pb': 67.38}
	Found duplicates of "Leakeite", with these properties :
			Density 3.11, Hardness 6.0, Elements {'Na': 8.14, 'Li': 0.82, 'Mg': 5.74, 'Fe': 13.19, 'Si': 26.53, 'H': 0.24, 'O': 45.34}
			Density 3.11, Hardness 6.0, Elements {'Na': 8.14, 'Li': 0.82, 'Mg': 5.74, 'Fe': 13.19, 'Si': 26.53, 'H': 0.24, 'O': 45.34}
	Found duplicates of "Lechatelierite", with these properties :
			Density 2.57, Hardness 6.5, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.57, Hardness 6.5, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.57, Hardness 6.5, Elements {'Si': 46.74, 'O': 53.26}
	Found duplicates of "Leisingite", with these properties :
			Density 3.41, Hardness 3.5, Elements {'Mg': 5.05, 'Zn': 2.72, 'Fe': 3.48, 'Cu': 19.81, 'Te': 26.52, 'H': 2.51, 'O': 39.9}
			Density 3.41, Hardness 3.5, Elements {'Mg': 5.05, 'Zn': 2.72, 'Fe': 3.48, 'Cu': 19.81, 'Te': 26.52, 'H': 2.51, 'O': 39.9}
	Found duplicates of "Potassicleakeite", with these properties :
			Density None, Hardness 5.0, Elements {'K': 2.68, 'Na': 6.56, 'Li': 0.55, 'Mg': 4.44, 'Mn': 6.27, 'V': 3.49, 'Fe': 6.37, 'Si': 25.63, 'H': 0.23, 'O': 43.8}
			Density None, Hardness 5.0, Elements {'K': 2.68, 'Na': 6.56, 'Li': 0.55, 'Mg': 4.44, 'Mn': 6.27, 'V': 3.49, 'Fe': 6.37, 'Si': 25.63, 'H': 0.23, 'O': 43.8}
			Density None, Hardness 5.0, Elements {'K': 2.68, 'Na': 6.56, 'Li': 0.55, 'Mg': 4.44, 'Mn': 6.27, 'V': 3.49, 'Fe': 6.37, 'Si': 25.63, 'H': 0.23, 'O': 43.8}
	Found duplicates of "Lemanskiite", with these properties :
			Density 3.78, Hardness 2.5, Elements {'Na': 2.25, 'Ca': 3.78, 'Cu': 30.01, 'As': 28.25, 'H': 0.94, 'Cl': 3.21, 'O': 31.57}
			Density 3.78, Hardness 2.5, Elements {'Na': 2.25, 'Ca': 3.78, 'Cu': 30.01, 'As': 28.25, 'H': 0.94, 'Cl': 3.21, 'O': 31.57}
	Found duplicates of "Lemmleinite-Ba", with these properties :
			Density 3.03, Hardness None, Elements {'K': 5.79, 'Ba': 11.54, 'Na': 3.96, 'Mg': 0.19, 'Ti': 15.14, 'Mn': 1.32, 'Nb': 0.37, 'Fe': 0.22, 'Si': 17.98, 'H': 0.91, 'O': 42.58}
			Density 3.03, Hardness None, Elements {'K': 5.79, 'Ba': 11.54, 'Na': 3.96, 'Mg': 0.19, 'Ti': 15.14, 'Mn': 1.32, 'Nb': 0.37, 'Fe': 0.22, 'Si': 17.98, 'H': 0.91, 'O': 42.58}
	Found duplicates of "Lemmleinite-K", with these properties :
			Density 2.8, Hardness 5.0, Elements {'K': 13.1, 'Na': 3.85, 'Ti': 11.23, 'Nb': 9.34, 'Si': 18.83, 'H': 0.74, 'O': 42.9}
			Density 2.8, Hardness 5.0, Elements {'K': 13.1, 'Na': 3.85, 'Ti': 11.23, 'Nb': 9.34, 'Si': 18.83, 'H': 0.74, 'O': 42.9}
			Density 2.8, Hardness 5.0, Elements {'K': 13.1, 'Na': 3.85, 'Ti': 11.23, 'Nb': 9.34, 'Si': 18.83, 'H': 0.74, 'O': 42.9}
	Found duplicates of "Lemoynite", with these properties :
			Density 2.29, Hardness 4.0, Elements {'K': 3.27, 'Na': 2.35, 'Ca': 3.35, 'Zr': 15.27, 'Nb': 0.86, 'Fe': 0.52, 'Si': 26.12, 'H': 1.07, 'O': 47.17}
			Density 2.29, Hardness 4.0, Elements {'K': 3.27, 'Na': 2.35, 'Ca': 3.35, 'Zr': 15.27, 'Nb': 0.86, 'Fe': 0.52, 'Si': 26.12, 'H': 1.07, 'O': 47.17}
	Found duplicates of "Lenaite", with these properties :
			Density 4.57, Hardness 4.25, Elements {'Fe': 24.51, 'Ag': 47.34, 'S': 28.15}
			Density 4.57, Hardness 4.25, Elements {'Fe': 24.51, 'Ag': 47.34, 'S': 28.15}
	Found duplicates of "Leogangite", with these properties :
			Density None, Hardness None, Elements {'Cu': 40.93, 'Si': 0.07, 'As': 19.22, 'H': 1.55, 'S': 2.06, 'O': 36.17}
			Density None, Hardness None, Elements {'Cu': 40.93, 'Si': 0.07, 'As': 19.22, 'H': 1.55, 'S': 2.06, 'O': 36.17}
	Found duplicates of "Lepidocrocite", with these properties :
			Density 4.0, Hardness 5.0, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
			Density 4.0, Hardness 5.0, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
			Density 4.0, Hardness 5.0, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
			Density 4.0, Hardness 5.0, Elements {'Fe': 62.85, 'H': 1.13, 'O': 36.01}
	Found duplicates of "Lepidolite", with these properties :
			Density 2.84, Hardness 2.75, Elements {'K': 10.07, 'Li': 3.58, 'Al': 6.95, 'Si': 28.93, 'H': 0.26, 'O': 45.32, 'F': 4.89}
			Density 2.84, Hardness 2.75, Elements {'K': 10.07, 'Li': 3.58, 'Al': 6.95, 'Si': 28.93, 'H': 0.26, 'O': 45.32, 'F': 4.89}
	Found duplicates of "Lepkhenelmite-Zn", with these properties :
			Density 2.96, Hardness 5.0, Elements {'K': 1.64, 'Ba': 9.91, 'Na': 0.44, 'Sr': 1.53, 'Ca': 0.83, 'Mg': 0.02, 'Ti': 11.16, 'Mn': 0.61, 'Nb': 7.44, 'Al': 0.21, 'Zn': 2.96, 'Fe': 0.18, 'Si': 17.37, 'H': 1.33, 'O': 44.38}
			Density 2.96, Hardness 5.0, Elements {'K': 1.64, 'Ba': 9.91, 'Na': 0.44, 'Sr': 1.53, 'Ca': 0.83, 'Mg': 0.02, 'Ti': 11.16, 'Mn': 0.61, 'Nb': 7.44, 'Al': 0.21, 'Zn': 2.96, 'Fe': 0.18, 'Si': 17.37, 'H': 1.33, 'O': 44.38}
	Found duplicates of "Clausthalite", with these properties :
			Density 8.19, Hardness 2.5, Elements {'Pb': 72.41, 'Se': 27.59}
			Density 8.19, Hardness 2.5, Elements {'Pb': 72.41, 'Se': 27.59}
			Density 8.19, Hardness 2.5, Elements {'Pb': 72.41, 'Se': 27.59}
			Density 8.19, Hardness 2.5, Elements {'Pb': 72.41, 'Se': 27.59}
	Found duplicates of "Britholite-Ce", with these properties :
			Density 4.45, Hardness 5.5, Elements {'Ca': 14.71, 'La': 7.29, 'Ce': 16.53, 'Th': 18.25, 'Si': 9.94, 'P': 2.03, 'H': 0.11, 'Nd': 3.78, 'O': 26.85, 'F': 0.5}
			Density 4.45, Hardness 5.5, Elements {'Ca': 14.71, 'La': 7.29, 'Ce': 16.53, 'Th': 18.25, 'Si': 9.94, 'P': 2.03, 'H': 0.11, 'Nd': 3.78, 'O': 26.85, 'F': 0.5}
			Density 4.45, Hardness 5.5, Elements {'Ca': 14.71, 'La': 7.29, 'Ce': 16.53, 'Th': 18.25, 'Si': 9.94, 'P': 2.03, 'H': 0.11, 'Nd': 3.78, 'O': 26.85, 'F': 0.5}
			Density 4.45, Hardness 5.5, Elements {'Ca': 14.71, 'La': 7.29, 'Ce': 16.53, 'Th': 18.25, 'Si': 9.94, 'P': 2.03, 'H': 0.11, 'Nd': 3.78, 'O': 26.85, 'F': 0.5}
			Density 4.45, Hardness 5.5, Elements {'Ca': 14.71, 'La': 7.29, 'Ce': 16.53, 'Th': 18.25, 'Si': 9.94, 'P': 2.03, 'H': 0.11, 'Nd': 3.78, 'O': 26.85, 'F': 0.5}
	Found duplicates of "Lesukite", with these properties :
			Density 1.87, Hardness None, Elements {'Al': 25.64, 'H': 4.31, 'Cl': 16.84, 'O': 53.21}
			Density 1.87, Hardness None, Elements {'Al': 25.64, 'H': 4.31, 'Cl': 16.84, 'O': 53.21}
	Found duplicates of "Cyanotrichite", with these properties :
			Density 2.84, Hardness 2.0, Elements {'Al': 8.38, 'Cu': 39.45, 'H': 2.5, 'S': 4.98, 'O': 44.7}
			Density 2.84, Hardness 2.0, Elements {'Al': 8.38, 'Cu': 39.45, 'H': 2.5, 'S': 4.98, 'O': 44.7}
	Found duplicates of "Leucite", with these properties :
			Density 2.47, Hardness 6.0, Elements {'K': 17.91, 'Al': 12.36, 'Si': 25.74, 'O': 43.99}
			Density 2.47, Hardness 6.0, Elements {'K': 17.91, 'Al': 12.36, 'Si': 25.74, 'O': 43.99}
	Found duplicates of "Aluminoceladonite", with these properties :
			Density None, Hardness None, Elements {'K': 9.66, 'Mg': 4.51, 'Al': 6.67, 'Fe': 3.45, 'Si': 27.76, 'H': 0.5, 'O': 47.45}
			Density None, Hardness None, Elements {'K': 9.66, 'Mg': 4.51, 'Al': 6.67, 'Fe': 3.45, 'Si': 27.76, 'H': 0.5, 'O': 47.45}
	Found duplicates of "Corundum", with these properties :
			Density 4.05, Hardness 9.0, Elements {'Al': 52.93, 'O': 47.07}
			Density 4.05, Hardness 9.0, Elements {'Al': 52.93, 'O': 47.07}
			Density 4.05, Hardness 9.0, Elements {'Al': 52.93, 'O': 47.07}
			Density 4.05, Hardness 9.0, Elements {'Al': 52.93, 'O': 47.07}
	Found duplicates of "Levinsonite-Y", with these properties :
			Density None, Hardness None, Elements {'La': 2.23, 'Sm': 2.41, 'Gd': 2.52, 'Y': 4.28, 'Al': 4.33, 'H': 3.88, 'C': 3.85, 'S': 10.29, 'Nd': 4.63, 'O': 61.59}
			Density None, Hardness None, Elements {'La': 2.23, 'Sm': 2.41, 'Gd': 2.52, 'Y': 4.28, 'Al': 4.33, 'H': 3.88, 'C': 3.85, 'S': 10.29, 'Nd': 4.63, 'O': 61.59}
	Found duplicates of "Levyclaudite", with these properties :
			Density None, Hardness 2.75, Elements {'Cu': 4.45, 'Sn': 18.98, 'Bi': 12.5, 'Sb': 1.99, 'Pb': 40.55, 'S': 21.54}
			Density None, Hardness 2.75, Elements {'Cu': 4.45, 'Sn': 18.98, 'Bi': 12.5, 'Sb': 1.99, 'Pb': 40.55, 'S': 21.54}
	Found duplicates of "Levyne-Ca", with these properties :
			Density 2.12, Hardness 4.25, Elements {'K': 0.52, 'Na': 0.99, 'Ca': 7.26, 'Al': 11.3, 'Si': 21.79, 'H': 2.23, 'O': 55.91}
			Density 2.12, Hardness 4.25, Elements {'K': 0.52, 'Na': 0.99, 'Ca': 7.26, 'Al': 11.3, 'Si': 21.79, 'H': 2.23, 'O': 55.91}
	Found duplicates of "Levyne-Na", with these properties :
			Density 2.12, Hardness 4.25, Elements {'K': 0.96, 'Na': 5.73, 'Ca': 2.32, 'Mg': 0.13, 'Al': 11.09, 'Si': 21.35, 'H': 2.36, 'O': 56.08}
			Density 2.12, Hardness 4.25, Elements {'K': 0.96, 'Na': 5.73, 'Ca': 2.32, 'Mg': 0.13, 'Al': 11.09, 'Si': 21.35, 'H': 2.36, 'O': 56.08}
			Density 2.12, Hardness 4.25, Elements {'K': 0.96, 'Na': 5.73, 'Ca': 2.32, 'Mg': 0.13, 'Al': 11.09, 'Si': 21.35, 'H': 2.36, 'O': 56.08}
	Found duplicates of "Carbonate-fluorapatite", with these properties :
			Density 3.12, Hardness 5.0, Elements {'Ca': 41.16, 'P': 15.91, 'C': 1.23, 'O': 37.79, 'F': 3.9}
			Density 3.12, Hardness 5.0, Elements {'Ca': 41.16, 'P': 15.91, 'C': 1.23, 'O': 37.79, 'F': 3.9}
			Density 3.12, Hardness 5.0, Elements {'Ca': 41.16, 'P': 15.91, 'C': 1.23, 'O': 37.79, 'F': 3.9}
			Density 3.12, Hardness 5.0, Elements {'Ca': 41.16, 'P': 15.91, 'C': 1.23, 'O': 37.79, 'F': 3.9}
	Found duplicates of "Libethenite", with these properties :
			Density 3.8, Hardness 4.0, Elements {'Cu': 53.16, 'P': 12.96, 'H': 0.42, 'O': 33.46}
			Density 3.8, Hardness 4.0, Elements {'Cu': 53.16, 'P': 12.96, 'H': 0.42, 'O': 33.46}
	Found duplicates of "Liebauite", with these properties :
			Density None, Hardness 5.5, Elements {'Ca': 10.86, 'Cu': 28.71, 'Si': 22.84, 'O': 37.59}
			Density None, Hardness 5.5, Elements {'Ca': 10.86, 'Cu': 28.71, 'Si': 22.84, 'O': 37.59}
	Found duplicates of "Lime", with these properties :
			Density 3.345, Hardness 3.5, Elements {'Ca': 71.47, 'O': 28.53}
			Density 3.345, Hardness 3.5, Elements {'Ca': 71.47, 'O': 28.53}
	Found duplicates of "Lindbergite", with these properties :
			Density 2.1, Hardness 2.5, Elements {'Na': 0.12, 'Mn': 32.86, 'Al': 0.15, 'H': 2.34, 'C': 12.56, 'O': 51.98}
			Density 2.1, Hardness 2.5, Elements {'Na': 0.12, 'Mn': 32.86, 'Al': 0.15, 'H': 2.34, 'C': 12.56, 'O': 51.98}
	Found duplicates of "Lindqvistite", with these properties :
			Density 5.76, Hardness 6.0, Elements {'Mg': 0.96, 'Mn': 4.05, 'Fe': 46.85, 'Si': 0.16, 'Pb': 23.49, 'O': 24.49}
			Density 5.76, Hardness 6.0, Elements {'Mg': 0.96, 'Mn': 4.05, 'Fe': 46.85, 'Si': 0.16, 'Pb': 23.49, 'O': 24.49}
	Found duplicates of "Lindstromite", with these properties :
			Density 7.01, Hardness 3.25, Elements {'Cu': 6.92, 'Bi': 53.08, 'Pb': 22.55, 'S': 17.45}
			Density 7.01, Hardness 3.25, Elements {'Cu': 6.92, 'Bi': 53.08, 'Pb': 22.55, 'S': 17.45}
	Found duplicates of "Brabantite", with these properties :
			Density 4.72, Hardness 5.5, Elements {'Ca': 8.67, 'Th': 50.22, 'P': 13.41, 'O': 27.7}
			Density 4.72, Hardness 5.5, Elements {'Ca': 8.67, 'Th': 50.22, 'P': 13.41, 'O': 27.7}
	Found duplicates of "Lingunite", with these properties :
			Density None, Hardness None, Elements {'Na': 4.25, 'Ca': 7.4, 'Al': 9.96, 'Si': 31.12, 'O': 47.27}
			Density None, Hardness None, Elements {'Na': 4.25, 'Ca': 7.4, 'Al': 9.96, 'Si': 31.12, 'O': 47.27}
	Found duplicates of "Linnaeite", with these properties :
			Density 4.8, Hardness 5.0, Elements {'Co': 57.95, 'S': 42.05}
			Density 4.8, Hardness 5.0, Elements {'Co': 57.95, 'S': 42.05}
	Found duplicates of "Lintisite", with these properties :
			Density 2.77, Hardness 5.5, Elements {'Na': 12.68, 'Li': 1.28, 'Ti': 17.6, 'Si': 20.65, 'H': 0.74, 'O': 47.05}
			Density 2.77, Hardness 5.5, Elements {'Na': 12.68, 'Li': 1.28, 'Ti': 17.6, 'Si': 20.65, 'H': 0.74, 'O': 47.05}
	Found duplicates of "Lishizhenite", with these properties :
			Density 2.206, Hardness 3.5, Elements {'Zn': 8.04, 'Fe': 13.73, 'H': 3.47, 'S': 15.77, 'O': 59.0}
			Density 2.206, Hardness 3.5, Elements {'Zn': 8.04, 'Fe': 13.73, 'H': 3.47, 'S': 15.77, 'O': 59.0}
	Found duplicates of "Lisiguangite", with these properties :
			Density None, Hardness 2.5, Elements {'Cu': 12.84, 'Bi': 37.24, 'Pd': 2.74, 'Pt': 29.75, 'S': 17.44}
			Density None, Hardness 2.5, Elements {'Cu': 12.84, 'Bi': 37.24, 'Pd': 2.74, 'Pt': 29.75, 'S': 17.44}
	Found duplicates of "Lisitsynite", with these properties :
			Density 2.74, Hardness 5.5, Elements {'K': 19.35, 'Si': 27.8, 'B': 5.35, 'O': 47.51}
			Density 2.74, Hardness 5.5, Elements {'K': 19.35, 'Si': 27.8, 'B': 5.35, 'O': 47.51}
	Found duplicates of "Litidionite", with these properties :
			Density 2.75, Hardness 5.5, Elements {'K': 9.82, 'Na': 5.78, 'Cu': 15.97, 'Si': 28.23, 'O': 40.2}
			Density 2.75, Hardness 5.5, Elements {'K': 9.82, 'Na': 5.78, 'Cu': 15.97, 'Si': 28.23, 'O': 40.2}
	Found duplicates of "Lithiomarsturite", with these properties :
			Density 3.32, Hardness 6.0, Elements {'Li': 1.2, 'Ca': 13.86, 'Mn': 19.0, 'Si': 24.28, 'H': 0.17, 'O': 41.49}
			Density 3.32, Hardness 6.0, Elements {'Li': 1.2, 'Ca': 13.86, 'Mn': 19.0, 'Si': 24.28, 'H': 0.17, 'O': 41.49}
	Found duplicates of "Zinnwaldite", with these properties :
			Density 3.0, Hardness 3.75, Elements {'K': 8.94, 'Li': 1.59, 'Al': 12.35, 'Fe': 12.78, 'Si': 19.28, 'H': 0.12, 'O': 38.43, 'F': 6.52}
			Density 3.0, Hardness 3.75, Elements {'K': 8.94, 'Li': 1.59, 'Al': 12.35, 'Fe': 12.78, 'Si': 19.28, 'H': 0.12, 'O': 38.43, 'F': 6.52}
	Found duplicates of "Lithiowodginite", with these properties :
			Density 7.5, Hardness 5.5, Elements {'Li': 1.02, 'Ta': 80.09, 'O': 18.88}
			Density 7.5, Hardness 5.5, Elements {'Li': 1.02, 'Ta': 80.09, 'O': 18.88}
	Found duplicates of "Litvinskite", with these properties :
			Density 2.61, Hardness 5.0, Elements {'Na': 9.29, 'Zr': 14.74, 'Mn': 1.33, 'Si': 27.23, 'H': 0.88, 'O': 46.53}
			Density 2.61, Hardness 5.0, Elements {'Na': 9.29, 'Zr': 14.74, 'Mn': 1.33, 'Si': 27.23, 'H': 0.88, 'O': 46.53}
	Found duplicates of "Liveingite", with these properties :
			Density 5.3, Hardness 3.0, Elements {'As': 26.07, 'Pb': 49.91, 'S': 24.03}
			Density 5.3, Hardness 3.0, Elements {'As': 26.07, 'Pb': 49.91, 'S': 24.03}
	Found duplicates of "Magnetite", with these properties :
			Density 5.15, Hardness 5.75, Elements {'Fe': 72.36, 'O': 27.64}
			Density 5.15, Hardness 5.75, Elements {'Fe': 72.36, 'O': 27.64}
			Density 5.15, Hardness 5.75, Elements {'Fe': 72.36, 'O': 27.64}
	Found duplicates of "Lollingite", with these properties :
			Density 7.4, Hardness 5.0, Elements {'Fe': 27.15, 'As': 72.85}
			Density 7.4, Hardness 5.0, Elements {'Fe': 27.15, 'As': 72.85}
	Found duplicates of "Loweite", with these properties :
			Density 2.37, Hardness 2.75, Elements {'Na': 14.04, 'Mg': 8.66, 'H': 1.54, 'S': 21.21, 'O': 54.55}
			Density 2.37, Hardness 2.75, Elements {'Na': 14.04, 'Mg': 8.66, 'H': 1.54, 'S': 21.21, 'O': 54.55}
	Found duplicates of "Londonite", with these properties :
			Density 3.34, Hardness 8.0, Elements {'Cs': 7.89, 'K': 1.84, 'Rb': 0.95, 'Na': 0.09, 'Li': 0.02, 'Ca': 0.1, 'Mn': 0.07, 'Be': 5.57, 'Al': 13.28, 'Fe': 0.07, 'Si': 0.03, 'B': 14.69, 'O': 55.4}
			Density 3.34, Hardness 8.0, Elements {'Cs': 7.89, 'K': 1.84, 'Rb': 0.95, 'Na': 0.09, 'Li': 0.02, 'Ca': 0.1, 'Mn': 0.07, 'Be': 5.57, 'Al': 13.28, 'Fe': 0.07, 'Si': 0.03, 'B': 14.69, 'O': 55.4}
	Found duplicates of "Lorenzenite", with these properties :
			Density 3.4, Hardness 6.0, Elements {'Na': 13.45, 'Ti': 28.01, 'Si': 16.43, 'O': 42.12}
			Density None, Hardness None, Elements {'Na': 13.45, 'Ti': 28.01, 'Si': 16.43, 'O': 42.12}
	Found duplicates of "Romerite", with these properties :
			Density 2.15, Hardness 3.25, Elements {'Fe': 20.92, 'H': 3.49, 'S': 15.99, 'O': 59.6}
			Density 2.15, Hardness 3.25, Elements {'Fe': 20.92, 'H': 3.49, 'S': 15.99, 'O': 59.6}
			Density 2.15, Hardness 3.25, Elements {'Fe': 20.92, 'H': 3.49, 'S': 15.99, 'O': 59.6}
	Found duplicates of "Luberoite", with these properties :
			Density 13.02, Hardness 5.25, Elements {'Pt': 75.54, 'Se': 24.46}
			Density 13.02, Hardness 5.25, Elements {'Pt': 75.54, 'Se': 24.46}
	Found duplicates of "Variscite", with these properties :
			Density 2.5, Hardness 4.5, Elements {'Al': 17.08, 'P': 19.61, 'H': 2.55, 'O': 60.76}
			Density 2.5, Hardness 4.5, Elements {'Al': 17.08, 'P': 19.61, 'H': 2.55, 'O': 60.76}
			Density 2.5, Hardness 4.5, Elements {'Al': 17.08, 'P': 19.61, 'H': 2.55, 'O': 60.76}
			Density 2.5, Hardness 4.5, Elements {'Al': 17.08, 'P': 19.61, 'H': 2.55, 'O': 60.76}
	Found duplicates of "Ludlockite", with these properties :
			Density 4.37, Hardness 1.75, Elements {'Fe': 17.16, 'As': 48.45, 'Pb': 3.35, 'O': 31.04}
			Density 4.37, Hardness 1.75, Elements {'Fe': 17.16, 'As': 48.45, 'Pb': 3.35, 'O': 31.04}
	Found duplicates of "Ludwigite", with these properties :
			Density 3.85, Hardness 5.5, Elements {'Mg': 24.89, 'Fe': 28.6, 'B': 5.54, 'O': 40.97}
			Density 3.85, Hardness 5.5, Elements {'Mg': 24.89, 'Fe': 28.6, 'B': 5.54, 'O': 40.97}
	Found duplicates of "Luneburgite", with these properties :
			Density 2.05, Hardness 2.0, Elements {'Mg': 15.3, 'B': 4.54, 'P': 13.0, 'H': 3.38, 'O': 63.78}
			Density 2.05, Hardness 2.0, Elements {'Mg': 15.3, 'B': 4.54, 'P': 13.0, 'H': 3.38, 'O': 63.78}
	Found duplicates of "Lukechangite-Ce", with these properties :
			Density 3.97, Hardness 4.5, Elements {'Na': 11.34, 'Ce': 46.07, 'C': 7.9, 'O': 31.57, 'F': 3.12}
			Density 3.97, Hardness 4.5, Elements {'Na': 11.34, 'Ce': 46.07, 'C': 7.9, 'O': 31.57, 'F': 3.12}
	Found duplicates of "Lukrahnite", with these properties :
			Density None, Hardness 5.0, Elements {'Ca': 8.55, 'Zn': 6.98, 'Fe': 9.53, 'Cu': 8.13, 'As': 31.97, 'H': 0.71, 'O': 34.13}
			Density None, Hardness 5.0, Elements {'Ca': 8.55, 'Zn': 6.98, 'Fe': 9.53, 'Cu': 8.13, 'As': 31.97, 'H': 0.71, 'O': 34.13}
	Found duplicates of "Lulzacite", with these properties :
			Density 3.55, Hardness 5.75, Elements {'Sr': 17.97, 'Mg': 1.99, 'Al': 11.06, 'Fe': 12.6, 'P': 12.7, 'H': 1.03, 'O': 42.65}
			Density 3.55, Hardness 5.75, Elements {'Sr': 17.97, 'Mg': 1.99, 'Al': 11.06, 'Fe': 12.6, 'P': 12.7, 'H': 1.03, 'O': 42.65}
	Found duplicates of "Pseudomalachite", with these properties :
			Density None, Hardness None, Elements {'Cu': 60.56, 'P': 11.81, 'H': 0.19, 'O': 27.44}
			Density None, Hardness None, Elements {'Cu': 60.56, 'P': 11.81, 'H': 0.19, 'O': 27.44}
			Density None, Hardness None, Elements {'Cu': 60.56, 'P': 11.81, 'H': 0.19, 'O': 27.44}
			Density None, Hardness None, Elements {'Cu': 60.56, 'P': 11.81, 'H': 0.19, 'O': 27.44}
			Density None, Hardness None, Elements {'Cu': 60.56, 'P': 11.81, 'H': 0.19, 'O': 27.44}
	Found duplicates of "Luobusaite", with these properties :
			Density None, Hardness 7.0, Elements {'Fe': 45.21, 'Si': 54.79}
			Density None, Hardness 7.0, Elements {'Fe': 45.21, 'Si': 54.79}
	Found duplicates of "Lutecite", with these properties :
			Density 2.54, Hardness 6.0, Elements {'Si': 46.74, 'O': 53.26}
			Density 2.54, Hardness 6.0, Elements {'Si': 46.74, 'O': 53.26}
	Found duplicates of "Euxenite-Y", with these properties :
			Density 4.84, Hardness 6.5, Elements {'Ca': 2.04, 'Ce': 3.57, 'Y': 15.86, 'Ta': 18.45, 'Ti': 2.44, 'Nb': 33.16, 'O': 24.47}
			Density 4.84, Hardness 6.5, Elements {'Ca': 2.04, 'Ce': 3.57, 'Y': 15.86, 'Ta': 18.45, 'Ti': 2.44, 'Nb': 33.16, 'O': 24.47}
	Found duplicates of "Mackelveyite-Y", with these properties :
			Density None, Hardness None, Elements {'Ba': 45.77, 'Na': 2.55, 'Ca': 4.45, 'Y': 9.88, 'H': 1.34, 'C': 4.0, 'O': 32.0}
			Density None, Hardness None, Elements {'Ba': 45.77, 'Na': 2.55, 'Ca': 4.45, 'Y': 9.88, 'H': 1.34, 'C': 4.0, 'O': 32.0}
	Found duplicates of "Thorogummite", with these properties :
			Density 5.4, Hardness 5.5, Elements {'Th': 72.13, 'Si': 7.86, 'H': 0.13, 'O': 19.89}
			Density 5.4, Hardness 5.5, Elements {'Th': 72.13, 'Si': 7.86, 'H': 0.13, 'O': 19.89}
	Found duplicates of "Madocite", with these properties :
			Density 5.98, Hardness 3.0, Elements {'Sb': 24.91, 'As': 4.38, 'Pb': 51.49, 'S': 19.22}
			Density 5.98, Hardness 3.0, Elements {'Sb': 24.91, 'As': 4.38, 'Pb': 51.49, 'S': 19.22}
	Found duplicates of "Makinenite", with these properties :
			Density None, Hardness 2.75, Elements {'Ni': 42.64, 'Se': 57.36}
			Density None, Hardness 2.75, Elements {'Ni': 42.64, 'Se': 57.36}
	Found duplicates of "Maghrebite", with these properties :
			Density None, Hardness None, Elements {'Mg': 4.55, 'Al': 10.1, 'As': 28.05, 'H': 3.4, 'O': 53.91}
			Density None, Hardness None, Elements {'Mg': 4.55, 'Al': 10.1, 'As': 28.05, 'H': 3.4, 'O': 53.91}
	Found duplicates of "Pickeringite", with these properties :
			Density 1.82, Hardness 1.75, Elements {'Mg': 2.83, 'Al': 6.28, 'H': 5.16, 'S': 14.93, 'O': 70.79}
			Density 1.82, Hardness 1.75, Elements {'Mg': 2.83, 'Al': 6.28, 'H': 5.16, 'S': 14.93, 'O': 70.79}
	Found duplicates of "Magnesiokatophorite", with these properties :
			Density 3.35, Hardness 5.0, Elements {'Na': 5.61, 'Ca': 4.89, 'Mg': 11.86, 'Al': 6.58, 'Si': 23.98, 'H': 0.25, 'O': 46.84}
			Density 3.35, Hardness 5.0, Elements {'Na': 5.61, 'Ca': 4.89, 'Mg': 11.86, 'Al': 6.58, 'Si': 23.98, 'H': 0.25, 'O': 46.84}
			Density 3.35, Hardness 5.0, Elements {'Na': 5.61, 'Ca': 4.89, 'Mg': 11.86, 'Al': 6.58, 'Si': 23.98, 'H': 0.25, 'O': 46.84}
	Found duplicates of "Axinite-Mg", with these properties :
			Density 3.28, Hardness 6.75, Elements {'Ca': 14.88, 'Mg': 4.51, 'Al': 10.02, 'Si': 20.86, 'B': 2.01, 'H': 0.19, 'O': 47.53}
			Density 3.28, Hardness 6.75, Elements {'Ca': 14.88, 'Mg': 4.51, 'Al': 10.02, 'Si': 20.86, 'B': 2.01, 'H': 0.19, 'O': 47.53}
	Found duplicates of "Magnesiotaramite", with these properties :
			Density None, Hardness 5.5, Elements {'Na': 5.41, 'Ca': 4.71, 'Mg': 8.58, 'Al': 9.52, 'Fe': 6.57, 'Si': 19.82, 'H': 0.24, 'O': 45.16}
			Density None, Hardness 5.5, Elements {'Na': 5.41, 'Ca': 4.71, 'Mg': 8.58, 'Al': 9.52, 'Fe': 6.57, 'Si': 19.82, 'H': 0.24, 'O': 45.16}
	Found duplicates of "Magnesioastrophyllite", with these properties :
			Density 3.32, Hardness 3.0, Elements {'K': 6.13, 'Na': 3.61, 'Mg': 3.81, 'Ti': 7.51, 'Mn': 2.15, 'Fe': 19.71, 'Si': 17.62, 'H': 0.32, 'O': 37.64, 'F': 1.49}
			Density 3.32, Hardness 3.0, Elements {'K': 6.13, 'Na': 3.61, 'Mg': 3.81, 'Ti': 7.51, 'Mn': 2.15, 'Fe': 19.71, 'Si': 17.62, 'H': 0.32, 'O': 37.64, 'F': 1.49}
	Found duplicates of "Magnesiochloritoid", with these properties :
			Density 3.55, Hardness 6.5, Elements {'Mg': 11.03, 'Al': 24.49, 'Si': 12.74, 'H': 0.91, 'O': 50.82}
			Density 3.55, Hardness 6.5, Elements {'Mg': 11.03, 'Al': 24.49, 'Si': 12.74, 'H': 0.91, 'O': 50.82}
	Found duplicates of "Magnesiochlorophoenicite", with these properties :
			Density 3.37, Hardness 3.25, Elements {'Mg': 11.74, 'Mn': 8.85, 'Zn': 28.09, 'As': 16.09, 'H': 0.87, 'O': 34.36}
			Density 3.37, Hardness 3.25, Elements {'Mg': 11.74, 'Mn': 8.85, 'Zn': 28.09, 'As': 16.09, 'H': 0.87, 'O': 34.36}
	Found duplicates of "Magnesiochromite", with these properties :
			Density 4.2, Hardness 5.5, Elements {'Mg': 12.64, 'Cr': 54.08, 'O': 33.28}
			Density 4.2, Hardness 5.5, Elements {'Mg': 12.64, 'Cr': 54.08, 'O': 33.28}
	Found duplicates of "Columbite-Mg", with these properties :
			Density 5.04, Hardness 6.5, Elements {'Mg': 5.15, 'Ta': 10.95, 'Ti': 2.9, 'Mn': 1.66, 'Nb': 47.79, 'Al': 0.82, 'Fe': 1.69, 'O': 29.05}
			Density 5.04, Hardness 6.5, Elements {'Mg': 5.15, 'Ta': 10.95, 'Ti': 2.9, 'Mn': 1.66, 'Nb': 47.79, 'Al': 0.82, 'Fe': 1.69, 'O': 29.05}
			Density 5.04, Hardness 6.5, Elements {'Mg': 5.15, 'Ta': 10.95, 'Ti': 2.9, 'Mn': 1.66, 'Nb': 47.79, 'Al': 0.82, 'Fe': 1.69, 'O': 29.05}
			Density 5.04, Hardness 6.5, Elements {'Mg': 5.15, 'Ta': 10.95, 'Ti': 2.9, 'Mn': 1.66, 'Nb': 47.79, 'Al': 0.82, 'Fe': 1.69, 'O': 29.05}
	Found duplicates of "Magnesiocoulsonite", with these properties :
			Density 4.3, Hardness 6.25, Elements {'Mg': 12.78, 'V': 53.57, 'O': 33.65}
			Density 4.3, Hardness 6.25, Elements {'Mg': 12.78, 'V': 53.57, 'O': 33.65}
	Found duplicates of "Magnesiodumortierite", with these properties :
			Density 3.22, Hardness 7.5, Elements {'Mg': 4.25, 'Ti': 2.51, 'Al': 25.94, 'Si': 14.73, 'B': 1.89, 'H': 0.35, 'O': 50.34}
			Density 3.22, Hardness 7.5, Elements {'Mg': 4.25, 'Ti': 2.51, 'Al': 25.94, 'Si': 14.73, 'B': 1.89, 'H': 0.35, 'O': 50.34}
	Found duplicates of "Ferri-magnesiotaramite", with these properties :
			Density None, Hardness None, Elements {'Na': 5.23, 'Ca': 4.56, 'Mg': 8.29, 'Al': 6.14, 'Fe': 12.7, 'Si': 19.17, 'H': 0.23, 'O': 43.68}
			Density None, Hardness None, Elements {'Na': 5.23, 'Ca': 4.56, 'Mg': 8.29, 'Al': 6.14, 'Fe': 12.7, 'Si': 19.17, 'H': 0.23, 'O': 43.68}
	Found duplicates of "Magnesioferrite", with these properties :
			Density 4.65, Hardness 6.25, Elements {'Mg': 12.15, 'Fe': 55.85, 'O': 32.0}
			Density 4.65, Hardness 6.25, Elements {'Mg': 12.15, 'Fe': 55.85, 'O': 32.0}
			Density 4.65, Hardness 6.25, Elements {'Mg': 12.15, 'Fe': 55.85, 'O': 32.0}
	Found duplicates of "Magnesiofoitite", with these properties :
			Density None, Hardness 7.0, Elements {'Mg': 3.89, 'Al': 21.31, 'Si': 17.98, 'B': 3.46, 'H': 0.43, 'O': 52.93}
			Density None, Hardness 7.0, Elements {'Mg': 3.89, 'Al': 21.31, 'Si': 17.98, 'B': 3.46, 'H': 0.43, 'O': 52.93}
	Found duplicates of "Magnesiohogbomite-2N2S", with these properties :
			Density 3.81, Hardness 6.5, Elements {'Mg': 3.45, 'Ti': 3.4, 'Al': 31.89, 'Zn': 3.09, 'Fe': 17.16, 'H': 0.17, 'O': 40.85}
			Density 3.81, Hardness 6.5, Elements {'Mg': 3.45, 'Ti': 3.4, 'Al': 31.89, 'Zn': 3.09, 'Fe': 17.16, 'H': 0.17, 'O': 40.85}
			Density 3.81, Hardness 6.5, Elements {'Mg': 3.45, 'Ti': 3.4, 'Al': 31.89, 'Zn': 3.09, 'Fe': 17.16, 'H': 0.17, 'O': 40.85}
	Found duplicates of "Magnesiohogbomite-2N3S", with these properties :
			Density 3.81, Hardness 6.5, Elements {'Mg': 2.88, 'Ti': 3.4, 'Al': 31.34, 'Zn': 3.1, 'Fe': 18.53, 'H': 0.17, 'O': 40.58}
			Density 3.81, Hardness 6.5, Elements {'Mg': 2.88, 'Ti': 3.4, 'Al': 31.34, 'Zn': 3.1, 'Fe': 18.53, 'H': 0.17, 'O': 40.58}
	Found duplicates of "Magnesiohogbomite-6N6S", with these properties :
			Density 3.81, Hardness 6.5, Elements {'Mg': 8.19, 'Ti': 4.84, 'Al': 36.35, 'Fe': 7.52, 'O': 43.11}
			Density 3.81, Hardness 6.5, Elements {'Mg': 8.19, 'Ti': 4.84, 'Al': 36.35, 'Fe': 7.52, 'O': 43.11}
			Density 3.81, Hardness 6.5, Elements {'Mg': 8.19, 'Ti': 4.84, 'Al': 36.35, 'Fe': 7.52, 'O': 43.11}
	Found duplicates of "Magnesiohornblende", with these properties :
			Density 3.23, Hardness 5.5, Elements {'Ca': 9.76, 'Mg': 11.84, 'Al': 5.75, 'Fe': 1.7, 'Si': 23.94, 'H': 0.25, 'O': 46.76}
			Density 3.23, Hardness 5.5, Elements {'Ca': 9.76, 'Mg': 11.84, 'Al': 5.75, 'Fe': 1.7, 'Si': 23.94, 'H': 0.25, 'O': 46.76}
			Density 3.23, Hardness 5.5, Elements {'Ca': 9.76, 'Mg': 11.84, 'Al': 5.75, 'Fe': 1.7, 'Si': 23.94, 'H': 0.25, 'O': 46.76}
	Found duplicates of "Magnesiopascoite", with these properties :
			Density 2.43, Hardness 2.5, Elements {'Ca': 5.29, 'Mg': 1.54, 'V': 37.98, 'Zn': 0.2, 'Co': 0.04, 'H': 2.45, 'O': 52.49}
			Density 2.43, Hardness 2.5, Elements {'Ca': 5.29, 'Mg': 1.54, 'V': 37.98, 'Zn': 0.2, 'Co': 0.04, 'H': 2.45, 'O': 52.49}
	Found duplicates of "Magnesiosadanagaite", with these properties :
			Density None, Hardness 5.75, Elements {'K': 0.41, 'Na': 2.48, 'Ca': 8.98, 'Mg': 8.12, 'Ti': 1.62, 'Mn': 0.14, 'Al': 11.12, 'Cr': 0.01, 'Fe': 5.28, 'Si': 17.43, 'H': 0.23, 'O': 44.17}
			Density None, Hardness 5.75, Elements {'K': 0.41, 'Na': 2.48, 'Ca': 8.98, 'Mg': 8.12, 'Ti': 1.62, 'Mn': 0.14, 'Al': 11.12, 'Cr': 0.01, 'Fe': 5.28, 'Si': 17.43, 'H': 0.23, 'O': 44.17}
	Found duplicates of "Magnesiostaurolite", with these properties :
			Density None, Hardness 7.25, Elements {'Li': 0.42, 'Mg': 4.7, 'Ti': 0.12, 'Al': 30.36, 'Zn': 0.08, 'Fe': 0.57, 'Si': 14.31, 'H': 0.26, 'O': 49.19}
			Density None, Hardness 7.25, Elements {'Li': 0.42, 'Mg': 4.7, 'Ti': 0.12, 'Al': 30.36, 'Zn': 0.08, 'Fe': 0.57, 'Si': 14.31, 'H': 0.26, 'O': 49.19}
	Found duplicates of "Magnesiotaaffeite-2N2S", with these properties :
			Density 3.6, Hardness 8.25, Elements {'Mg': 13.17, 'Be': 1.63, 'Al': 38.98, 'O': 46.23}
			Density 3.6, Hardness 8.25, Elements {'Mg': 13.17, 'Be': 1.63, 'Al': 38.98, 'O': 46.23}
	Found duplicates of "Tantalite-Mg", with these properties :
			Density 6.7, Hardness 5.5, Elements {'Mg': 3.2, 'Ta': 51.06, 'Ti': 0.23, 'Mn': 0.65, 'Nb': 17.04, 'Fe': 5.25, 'O': 22.57}
			Density 6.7, Hardness 5.5, Elements {'Mg': 3.2, 'Ta': 51.06, 'Ti': 0.23, 'Mn': 0.65, 'Nb': 17.04, 'Fe': 5.25, 'O': 22.57}
			Density 6.7, Hardness 5.5, Elements {'Mg': 3.2, 'Ta': 51.06, 'Ti': 0.23, 'Mn': 0.65, 'Nb': 17.04, 'Fe': 5.25, 'O': 22.57}
	Found duplicates of "Magnesite", with these properties :
			Density 3.0, Hardness 4.0, Elements {'Mg': 28.83, 'C': 14.25, 'O': 56.93}
			Density 3.0, Hardness 4.0, Elements {'Mg': 28.83, 'C': 14.25, 'O': 56.93}
	Found duplicates of "Pyrrhotite", with these properties :
			Density 4.61, Hardness 3.75, Elements {'Fe': 62.33, 'S': 37.67}
			Density 4.61, Hardness 3.75, Elements {'Fe': 62.33, 'S': 37.67}
	Found duplicates of "Mahnertite", with these properties :
			Density 3.14, Hardness 2.5, Elements {'Na': 3.58, 'Ca': 0.85, 'Cu': 30.93, 'As': 26.52, 'H': 1.3, 'Cl': 3.89, 'O': 32.93}
			Density 3.14, Hardness 2.5, Elements {'Na': 3.58, 'Ca': 0.85, 'Cu': 30.93, 'As': 26.52, 'H': 1.3, 'Cl': 3.89, 'O': 32.93}
	Found duplicates of "Maikainite", with these properties :
			Density None, Hardness 4.0, Elements {'V': 0.12, 'Zn': 0.56, 'Ga': 0.15, 'Fe': 6.3, 'Cu': 42.23, 'Ge': 10.79, 'Mo': 5.18, 'As': 2.27, 'W': 1.23, 'S': 31.16}
			Density None, Hardness 4.0, Elements {'V': 0.12, 'Zn': 0.56, 'Ga': 0.15, 'Fe': 6.3, 'Cu': 42.23, 'Ge': 10.79, 'Mo': 5.18, 'As': 2.27, 'W': 1.23, 'S': 31.16}
	Found duplicates of "Makarochkinite", with these properties :
			Density 3.93, Hardness 5.75, Elements {'Na': 0.66, 'Ca': 7.54, 'Mg': 0.7, 'Ta': 0.15, 'Ti': 3.35, 'Mn': 0.82, 'Be': 0.94, 'Nb': 0.41, 'Al': 1.67, 'Fe': 32.62, 'Si': 14.44, 'O': 36.72}
			Density 3.93, Hardness 5.75, Elements {'Na': 0.66, 'Ca': 7.54, 'Mg': 0.7, 'Ta': 0.15, 'Ti': 3.35, 'Mn': 0.82, 'Be': 0.94, 'Nb': 0.41, 'Al': 1.67, 'Fe': 32.62, 'Si': 14.44, 'O': 36.72}
	Found duplicates of "Makovickyite", with these properties :
			Density 6.7, Hardness 3.5, Elements {'Ag': 10.11, 'Bi': 71.85, 'S': 18.04}
			Density 6.7, Hardness 3.5, Elements {'Ag': 10.11, 'Bi': 71.85, 'S': 18.04}
	Found duplicates of "Apatite-CaF", with these properties :
			Density 3.15, Hardness 5.0, Elements {'Ca': 39.74, 'P': 18.43, 'O': 38.07, 'F': 3.77}
			Density 3.15, Hardness 5.0, Elements {'Ca': 39.74, 'P': 18.43, 'O': 38.07, 'F': 3.77}
	Found duplicates of "Apophyllite-KF", with these properties :
			Density 2.34, Hardness 4.5, Elements {'K': 4.37, 'Na': 0.45, 'Ca': 21.08, 'Si': 29.55, 'H': 0.01, 'O': 42.29, 'F': 2.25}
			Density 2.34, Hardness 4.5, Elements {'K': 4.37, 'Na': 0.45, 'Ca': 21.08, 'Si': 29.55, 'H': 0.01, 'O': 42.29, 'F': 2.25}
	Found duplicates of "Fluorarrojadite-BaFe", with these properties :
			Density None, Hardness 4.5, Elements {'K': 0.55, 'Ba': 3.89, 'Na': 3.26, 'Ca': 1.89, 'Mg': 4.59, 'Mn': 7.79, 'Al': 1.27, 'Fe': 21.11, 'P': 17.56, 'H': 0.07, 'O': 36.66, 'F': 1.35}
			Density None, Hardness 4.5, Elements {'K': 0.55, 'Ba': 3.89, 'Na': 3.26, 'Ca': 1.89, 'Mg': 4.59, 'Mn': 7.79, 'Al': 1.27, 'Fe': 21.11, 'P': 17.56, 'H': 0.07, 'O': 36.66, 'F': 1.35}
	Found duplicates of "Fluorbritholite-Ce", with these properties :
			Density 4.66, Hardness 5.0, Elements {'Sr': 0.34, 'Ca': 10.15, 'La': 13.72, 'Ce': 25.3, 'Pr': 3.11, 'Sm': 0.59, 'Y': 1.04, 'Th': 1.51, 'Si': 9.78, 'P': 1.25, 'Nd': 5.81, 'O': 24.99, 'F': 2.42}
			Density 4.66, Hardness 5.0, Elements {'Sr': 0.34, 'Ca': 10.15, 'La': 13.72, 'Ce': 25.3, 'Pr': 3.11, 'Sm': 0.59, 'Y': 1.04, 'Th': 1.51, 'Si': 9.78, 'P': 1.25, 'Nd': 5.81, 'O': 24.99, 'F': 2.42}
	Found duplicates of "Fluorcalciobritholite", with these properties :
			Density 4.2, Hardness 5.5, Elements {'Sr': 0.25, 'Ca': 15.8, 'La': 10.56, 'Ce': 18.35, 'Pr': 1.59, 'Sm': 0.64, 'Gd': 0.66, 'Dy': 0.46, 'Y': 2.25, 'Er': 0.24, 'Th': 1.31, 'Yb': 0.49, 'Mn': 0.23, 'Si': 7.67, 'P': 4.62, 'Nd': 5.28, 'Cl': 0.05, 'O': 27.53, 'F': 2.03}
			Density 4.2, Hardness 5.5, Elements {'Sr': 0.25, 'Ca': 15.8, 'La': 10.56, 'Ce': 18.35, 'Pr': 1.59, 'Sm': 0.64, 'Gd': 0.66, 'Dy': 0.46, 'Y': 2.25, 'Er': 0.24, 'Th': 1.31, 'Yb': 0.49, 'Mn': 0.23, 'Si': 7.67, 'P': 4.62, 'Nd': 5.28, 'Cl': 0.05, 'O': 27.53, 'F': 2.03}
	Found duplicates of "Fluorcanasite", with these properties :
			Density None, Hardness None, Elements {'K': 9.04, 'Na': 5.31, 'Ca': 15.44, 'Si': 25.97, 'H': 0.16, 'O': 38.22, 'F': 5.86}
			Density None, Hardness None, Elements {'K': 9.04, 'Na': 5.31, 'Ca': 15.44, 'Si': 25.97, 'H': 0.16, 'O': 38.22, 'F': 5.86}
	Found duplicates of "Fluorcaphite", with these properties :
			Density 3.6, Hardness 5.0, Elements {'Na': 0.86, 'Sr': 19.58, 'Ca': 13.43, 'Ce': 20.87, 'P': 13.84, 'O': 28.6, 'F': 2.83}
			Density 3.6, Hardness 5.0, Elements {'Na': 0.86, 'Sr': 19.58, 'Ca': 13.43, 'Ce': 20.87, 'P': 13.84, 'O': 28.6, 'F': 2.83}
	Found duplicates of "Fluoro-edenite", with these properties :
			Density None, Hardness 5.5, Elements {'K': 0.93, 'Na': 2.47, 'Ca': 7.66, 'Mg': 13.65, 'Al': 1.93, 'Fe': 2.0, 'Si': 24.83, 'H': 0.04, 'O': 42.63, 'F': 3.86}
			Density None, Hardness 5.5, Elements {'K': 0.93, 'Na': 2.47, 'Ca': 7.66, 'Mg': 13.65, 'Al': 1.93, 'Fe': 2.0, 'Si': 24.83, 'H': 0.04, 'O': 42.63, 'F': 3.86}
			Density None, Hardness 5.5, Elements {'K': 0.93, 'Na': 2.47, 'Ca': 7.66, 'Mg': 13.65, 'Al': 1.93, 'Fe': 2.0, 'Si': 24.83, 'H': 0.04, 'O': 42.63, 'F': 3.86}
			Density None, Hardness 5.5, Elements {'K': 0.93, 'Na': 2.47, 'Ca': 7.66, 'Mg': 13.65, 'Al': 1.93, 'Fe': 2.0, 'Si': 24.83, 'H': 0.04, 'O': 42.63, 'F': 3.86}
	Found duplicates of "Ellestadite-F", with these properties :
			Density 3.05, Hardness 4.5, Elements {'Ca': 39.8, 'Si': 5.58, 'P': 6.15, 'H': 0.06, 'S': 6.37, 'Cl': 0.7, 'O': 39.08, 'F': 2.26}
			Density 3.05, Hardness 4.5, Elements {'Ca': 39.8, 'Si': 5.58, 'P': 6.15, 'H': 0.06, 'S': 6.37, 'Cl': 0.7, 'O': 39.08, 'F': 2.26}
	Found duplicates of "Fluorite", with these properties :
			Density 3.13, Hardness 4.0, Elements {'Ca': 51.33, 'F': 48.67}
			Density 3.13, Hardness 4.0, Elements {'Ca': 51.33, 'F': 48.67}
			Density 3.13, Hardness 4.0, Elements {'Ca': 51.33, 'F': 48.67}
	Found duplicates of "Fluornatromicrolite", with these properties :
			Density None, Hardness None, Elements {'Na': 4.84, 'Ca': 4.22, 'Ta': 63.45, 'Bi': 7.33, 'O': 16.83, 'F': 3.33}
			Density None, Hardness None, Elements {'Na': 4.84, 'Ca': 4.22, 'Ta': 63.45, 'Bi': 7.33, 'O': 16.83, 'F': 3.33}
	Found duplicates of "Fluoro-alumino-magnesiotaramite", with these properties :
			Density None, Hardness None, Elements {'Na': 4.58, 'Ca': 5.49, 'Mg': 5.84, 'Ti': 0.16, 'Mn': 0.12, 'Al': 9.29, 'Fe': 11.45, 'Si': 19.09, 'H': 0.11, 'O': 41.62, 'F': 2.24}
			Density None, Hardness None, Elements {'Na': 4.58, 'Ca': 5.49, 'Mg': 5.84, 'Ti': 0.16, 'Mn': 0.12, 'Al': 9.29, 'Fe': 11.45, 'Si': 19.09, 'H': 0.11, 'O': 41.62, 'F': 2.24}
	Found duplicates of "Fluoro-ferroleakeite", with these properties :
			Density 3.37, Hardness 6.0, Elements {'Na': 7.55, 'Li': 0.76, 'Fe': 24.44, 'Si': 24.58, 'O': 38.51, 'F': 4.16}
			Density 3.37, Hardness 6.0, Elements {'Na': 7.55, 'Li': 0.76, 'Fe': 24.44, 'Si': 24.58, 'O': 38.51, 'F': 4.16}
	Found duplicates of "Fluoro-magnesio-arfvedsonite", with these properties :
			Density 3.09, Hardness 5.5, Elements {'K': 1.42, 'Na': 5.55, 'Ca': 1.94, 'Mg': 12.03, 'Al': 0.65, 'Fe': 4.72, 'Si': 26.79, 'H': 0.1, 'O': 44.05, 'F': 2.75}
			Density 3.09, Hardness 5.5, Elements {'K': 1.42, 'Na': 5.55, 'Ca': 1.94, 'Mg': 12.03, 'Al': 0.65, 'Fe': 4.72, 'Si': 26.79, 'H': 0.1, 'O': 44.05, 'F': 2.75}
	Found duplicates of "Fluoro-magnesiohastingsite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 0.99, 'Na': 1.33, 'Ca': 10.04, 'Mg': 11.31, 'Ti': 0.72, 'Al': 6.98, 'Fe': 4.51, 'Si': 19.1, 'O': 40.64, 'F': 4.39}
			Density None, Hardness 6.0, Elements {'K': 0.99, 'Na': 1.33, 'Ca': 10.04, 'Mg': 11.31, 'Ti': 0.72, 'Al': 6.98, 'Fe': 4.51, 'Si': 19.1, 'O': 40.64, 'F': 4.39}
	Found duplicates of "Fluoro-potassichastingsite", with these properties :
			Density None, Hardness None, Elements {'K': 4.12, 'Ca': 8.46, 'Mg': 5.13, 'Al': 5.69, 'Fe': 17.68, 'Si': 17.78, 'O': 37.13, 'F': 4.01}
			Density None, Hardness None, Elements {'K': 4.12, 'Ca': 8.46, 'Mg': 5.13, 'Al': 5.69, 'Fe': 17.68, 'Si': 17.78, 'O': 37.13, 'F': 4.01}
	Found duplicates of "Fluoro-sodic-pedrizite", with these properties :
			Density 3.0, Hardness 5.5, Elements {'K': 0.05, 'Na': 1.98, 'Li': 2.3, 'Ca': 0.21, 'Mg': 5.69, 'Mn': 0.14, 'Al': 7.01, 'Zn': 0.08, 'Cr': 0.07, 'Fe': 3.74, 'Si': 28.77, 'H': 0.12, 'O': 47.15, 'F': 2.69}
			Density 3.0, Hardness 5.5, Elements {'K': 0.05, 'Na': 1.98, 'Li': 2.3, 'Ca': 0.21, 'Mg': 5.69, 'Mn': 0.14, 'Al': 7.01, 'Zn': 0.08, 'Cr': 0.07, 'Fe': 3.74, 'Si': 28.77, 'H': 0.12, 'O': 47.15, 'F': 2.69}
	Found duplicates of "Fluorocannilloite", with these properties :
			Density 3.05, Hardness 6.0, Elements {'Ca': 14.05, 'Mg': 11.36, 'Al': 12.61, 'Si': 16.41, 'O': 41.13, 'F': 4.44}
			Density 3.05, Hardness 6.0, Elements {'Ca': 14.05, 'Mg': 11.36, 'Al': 12.61, 'Si': 16.41, 'O': 41.13, 'F': 4.44}
	Found duplicates of "Fluoropargasite", with these properties :
			Density 3.18, Hardness 6.0, Elements {'K': 0.76, 'Na': 2.12, 'Ca': 8.84, 'Mg': 8.79, 'Ti': 0.54, 'Mn': 0.06, 'Al': 6.48, 'V': 0.12, 'Fe': 7.62, 'Si': 20.41, 'H': 0.08, 'Cl': 0.12, 'O': 41.33, 'F': 2.72}
			Density 3.18, Hardness 6.0, Elements {'K': 0.76, 'Na': 2.12, 'Ca': 8.84, 'Mg': 8.79, 'Ti': 0.54, 'Mn': 0.06, 'Al': 6.48, 'V': 0.12, 'Fe': 7.62, 'Si': 20.41, 'H': 0.08, 'Cl': 0.12, 'O': 41.33, 'F': 2.72}
	Found duplicates of "Fluorophlogopite", with these properties :
			Density None, Hardness 2.5, Elements {'K': 6.86, 'Na': 0.44, 'Li': 0.13, 'Mg': 16.83, 'Ti': 0.69, 'Mn': 0.13, 'Al': 5.06, 'Fe': 0.94, 'Si': 21.34, 'H': 0.02, 'O': 38.86, 'F': 8.68}
			Density None, Hardness 2.5, Elements {'K': 6.86, 'Na': 0.44, 'Li': 0.13, 'Mg': 16.83, 'Ti': 0.69, 'Mn': 0.13, 'Al': 5.06, 'Fe': 0.94, 'Si': 21.34, 'H': 0.02, 'O': 38.86, 'F': 8.68}
	Found duplicates of "Fluororichterite", with these properties :
			Density None, Hardness None, Elements {'Na': 5.59, 'Ca': 4.87, 'Mg': 14.78, 'Si': 27.33, 'O': 42.81, 'F': 4.62}
			Density None, Hardness None, Elements {'Na': 5.59, 'Ca': 4.87, 'Mg': 14.78, 'Si': 27.33, 'O': 42.81, 'F': 4.62}
	Found duplicates of "Fluorthalenite-Y", with these properties :
			Density 4.24, Hardness 4.5, Elements {'Y': 50.33, 'Si': 15.9, 'O': 30.19, 'F': 3.58}
			Density 4.24, Hardness 4.5, Elements {'Y': 50.33, 'Si': 15.9, 'O': 30.19, 'F': 3.58}
	Found duplicates of "Fluorvesuvianite", with these properties :
			Density 3.46, Hardness 6.0, Elements {'Ca': 26.19, 'Mg': 1.16, 'Mn': 0.08, 'Al': 9.59, 'Fe': 2.21, 'Si': 17.37, 'H': 0.06, 'O': 38.69, 'F': 4.67}
			Density 3.46, Hardness 6.0, Elements {'Ca': 26.19, 'Mg': 1.16, 'Mn': 0.08, 'Al': 9.59, 'Fe': 2.21, 'Si': 17.37, 'H': 0.06, 'O': 38.69, 'F': 4.67}
	Found duplicates of "Chondrodite", with these properties :
			Density 3.15, Hardness 6.25, Elements {'Mg': 23.85, 'Fe': 18.27, 'Si': 14.7, 'H': 0.13, 'O': 35.59, 'F': 7.46}
			Density 3.15, Hardness 6.25, Elements {'Mg': 23.85, 'Fe': 18.27, 'Si': 14.7, 'H': 0.13, 'O': 35.59, 'F': 7.46}
	Found duplicates of "Foitite", with these properties :
			Density 3.17, Hardness 7.0, Elements {'Na': 2.23, 'Al': 17.65, 'Fe': 12.18, 'Si': 16.33, 'B': 3.14, 'H': 0.39, 'O': 48.07}
			Density 3.17, Hardness 7.0, Elements {'Na': 2.23, 'Al': 17.65, 'Fe': 12.18, 'Si': 16.33, 'B': 3.14, 'H': 0.39, 'O': 48.07}
	Found duplicates of "Fontanite", with these properties :
			Density 4.1, Hardness 3.0, Elements {'Ca': 3.61, 'U': 64.32, 'H': 1.09, 'C': 2.16, 'O': 28.82}
			Density 4.1, Hardness 3.0, Elements {'Ca': 3.61, 'U': 64.32, 'H': 1.09, 'C': 2.16, 'O': 28.82}
	Found duplicates of "Pyrite", with these properties :
			Density 5.01, Hardness 6.5, Elements {'Fe': 46.55, 'S': 53.45}
			Density 5.01, Hardness 6.5, Elements {'Fe': 46.55, 'S': 53.45}
			Density 5.01, Hardness 6.5, Elements {'Fe': 46.55, 'S': 53.45}
	Found duplicates of "Connellite", with these properties :
			Density 3.4, Hardness 3.0, Elements {'Cu': 59.08, 'H': 1.87, 'S': 1.57, 'Cl': 6.94, 'O': 30.53}
			Density 3.4, Hardness 3.0, Elements {'Cu': 59.08, 'H': 1.87, 'S': 1.57, 'Cl': 6.94, 'O': 30.53}
			Density 3.4, Hardness 3.0, Elements {'Cu': 59.08, 'H': 1.87, 'S': 1.57, 'Cl': 6.94, 'O': 30.53}
	Found duplicates of "Footemineite", with these properties :
			Density None, Hardness 4.5, Elements {'Ba': 0.24, 'Sr': 0.23, 'Li': 0.11, 'Ca': 6.75, 'Mg': 0.11, 'Mn': 20.31, 'Be': 3.45, 'Al': 0.07, 'Fe': 2.59, 'Si': 0.2, 'P': 16.03, 'H': 1.47, 'O': 48.45}
			Density None, Hardness 4.5, Elements {'Ba': 0.24, 'Sr': 0.23, 'Li': 0.11, 'Ca': 6.75, 'Mg': 0.11, 'Mn': 20.31, 'Be': 3.45, 'Al': 0.07, 'Fe': 2.59, 'Si': 0.2, 'P': 16.03, 'H': 1.47, 'O': 48.45}
	Found duplicates of "Formicaite", with these properties :
			Density 1.9, Hardness 1.0, Elements {'Ca': 30.8, 'H': 1.55, 'C': 18.46, 'O': 49.19}
			Density 1.9, Hardness 1.0, Elements {'Ca': 30.8, 'H': 1.55, 'C': 18.46, 'O': 49.19}
	Found duplicates of "Fougerite", with these properties :
			Density None, Hardness None, Elements {'Mg': 4.69, 'Fe': 46.68, 'H': 3.37, 'O': 45.26}
			Density None, Hardness None, Elements {'Mg': 4.69, 'Fe': 46.68, 'H': 3.37, 'O': 45.26}
	Found duplicates of "Rhodonite", with these properties :
			Density 3.6, Hardness 6.0, Elements {'Ca': 1.55, 'Mg': 0.38, 'Mn': 38.29, 'Fe': 0.86, 'Si': 21.75, 'O': 37.17}
			Density 3.6, Hardness 6.0, Elements {'Ca': 1.55, 'Mg': 0.38, 'Mn': 38.29, 'Fe': 0.86, 'Si': 21.75, 'O': 37.17}
	Found duplicates of "Francisite", with these properties :
			Density None, Hardness 3.5, Elements {'Cu': 26.44, 'Bi': 28.99, 'Se': 21.9, 'Cl': 4.92, 'O': 17.75}
			Density None, Hardness 3.5, Elements {'Cu': 26.44, 'Bi': 28.99, 'Se': 21.9, 'Cl': 4.92, 'O': 17.75}
	Found duplicates of "Francoisite-Ce", with these properties :
			Density None, Hardness None, Elements {'Ca': 0.31, 'Ce': 6.61, 'U': 56.12, 'P': 4.87, 'H': 1.03, 'Nd': 3.4, 'O': 27.66}
			Density None, Hardness None, Elements {'Ca': 0.31, 'Ce': 6.61, 'U': 56.12, 'P': 4.87, 'H': 1.03, 'Nd': 3.4, 'O': 27.66}
	Found duplicates of "Franconite", with these properties :
			Density 2.72, Hardness 4.0, Elements {'Na': 6.08, 'Nb': 49.17, 'H': 2.4, 'O': 42.34}
			Density 2.72, Hardness 4.0, Elements {'Na': 6.08, 'Nb': 49.17, 'H': 2.4, 'O': 42.34}
	Found duplicates of "Frankamenite", with these properties :
			Density 2.68, Hardness 5.5, Elements {'K': 8.73, 'Na': 5.13, 'Ca': 15.74, 'Mn': 0.42, 'Si': 25.96, 'H': 0.23, 'O': 39.68, 'F': 4.1}
			Density 2.68, Hardness 5.5, Elements {'K': 8.73, 'Na': 5.13, 'Ca': 15.74, 'Mn': 0.42, 'Si': 25.96, 'H': 0.23, 'O': 39.68, 'F': 4.1}
	Found duplicates of "Frankhawthorneite", with these properties :
			Density 5.44, Hardness 3.25, Elements {'Cu': 36.03, 'Te': 36.18, 'H': 0.57, 'O': 27.22}
			Density 5.44, Hardness 3.25, Elements {'Cu': 36.03, 'Te': 36.18, 'H': 0.57, 'O': 27.22}
	Found duplicates of "Franklinphilite", with these properties :
			Density 2.7, Hardness 4.0, Elements {'K': 1.24, 'Na': 0.31, 'Mg': 3.91, 'Mn': 17.47, 'Al': 1.91, 'Zn': 4.78, 'Fe': 5.51, 'Si': 20.81, 'H': 0.8, 'O': 43.28}
			Density 2.7, Hardness 4.0, Elements {'K': 1.24, 'Na': 0.31, 'Mg': 3.91, 'Mn': 17.47, 'Al': 1.91, 'Zn': 4.78, 'Fe': 5.51, 'Si': 20.81, 'H': 0.8, 'O': 43.28}
	Found duplicates of "Fuenzalidaite", with these properties :
			Density 3.31, Hardness 2.75, Elements {'K': 6.53, 'Na': 4.94, 'Mg': 5.8, 'H': 0.58, 'S': 9.18, 'I': 36.33, 'O': 36.64}
			Density 3.31, Hardness 2.75, Elements {'K': 6.53, 'Na': 4.94, 'Mg': 5.8, 'H': 0.58, 'S': 9.18, 'I': 36.33, 'O': 36.64}
	Found duplicates of "Fullerite", with these properties :
			Density 1.95, Hardness 3.5, Elements {'C': 100.0}
			Density 1.95, Hardness 3.5, Elements {'C': 100.0}
			Density 1.95, Hardness 3.5, Elements {'C': 100.0}
	Found duplicates of "Gabrielite", with these properties :
			Density None, Hardness 1.75, Elements {'Tl': 37.37, 'Cu': 12.68, 'Ag': 8.47, 'Sb': 1.8, 'As': 18.96, 'S': 20.73}
			Density None, Hardness 1.75, Elements {'Tl': 37.37, 'Cu': 12.68, 'Ag': 8.47, 'Sb': 1.8, 'As': 18.96, 'S': 20.73}
	Found duplicates of "Gahnite", with these properties :
			Density 4.3, Hardness 8.0, Elements {'Al': 29.43, 'Zn': 35.66, 'O': 34.9}
			Density 4.3, Hardness 8.0, Elements {'Al': 29.43, 'Zn': 35.66, 'O': 34.9}
	Found duplicates of "Gaidonnayite", with these properties :
			Density 2.67, Hardness 5.0, Elements {'Na': 11.45, 'Zr': 22.72, 'Si': 20.99, 'H': 1.0, 'O': 43.84}
			Density 2.67, Hardness 5.0, Elements {'Na': 11.45, 'Zr': 22.72, 'Si': 20.99, 'H': 1.0, 'O': 43.84}
	Found duplicates of "Gaitite", with these properties :
			Density 3.81, Hardness 5.0, Elements {'Ca': 17.45, 'Zn': 14.23, 'As': 32.62, 'H': 0.88, 'O': 34.83}
			Density 3.81, Hardness 5.0, Elements {'Ca': 17.45, 'Zn': 14.23, 'As': 32.62, 'H': 0.88, 'O': 34.83}
	Found duplicates of "Galgenbergite-Ce", with these properties :
			Density None, Hardness None, Elements {'Ca': 6.87, 'La': 10.1, 'Ce': 25.22, 'Pr': 2.93, 'H': 0.35, 'C': 8.31, 'Nd': 10.23, 'O': 35.99}
			Density None, Hardness None, Elements {'Ca': 6.87, 'La': 10.1, 'Ce': 25.22, 'Pr': 2.93, 'H': 0.35, 'C': 8.31, 'Nd': 10.23, 'O': 35.99}
	Found duplicates of "Galileiite", with these properties :
			Density 3.91, Hardness 4.0, Elements {'Na': 4.33, 'Fe': 42.05, 'P': 17.49, 'O': 36.14}
			Density 3.91, Hardness 4.0, Elements {'Na': 4.33, 'Fe': 42.05, 'P': 17.49, 'O': 36.14}
	Found duplicates of "Gallobeudantite", with these properties :
			Density 4.58, Hardness 4.0, Elements {'Al': 2.25, 'Zn': 0.91, 'Ga': 14.55, 'Fe': 6.22, 'As': 11.47, 'H': 0.83, 'Pb': 28.83, 'S': 4.01, 'O': 30.94}
			Density 4.58, Hardness 4.0, Elements {'Al': 2.25, 'Zn': 0.91, 'Ga': 14.55, 'Fe': 6.22, 'As': 11.47, 'H': 0.83, 'Pb': 28.83, 'S': 4.01, 'O': 30.94}
	Found duplicates of "Smithsonite", with these properties :
			Density 4.45, Hardness 4.5, Elements {'Zn': 52.15, 'C': 9.58, 'O': 38.28}
			Density 4.45, Hardness 4.5, Elements {'Zn': 52.15, 'C': 9.58, 'O': 38.28}
			Density 4.45, Hardness 4.5, Elements {'Zn': 52.15, 'C': 9.58, 'O': 38.28}
	Found duplicates of "Gaotaiite", with these properties :
			Density 9.97, Hardness 3.0, Elements {'Te': 63.9, 'Ir': 36.1}
			Density 9.97, Hardness 3.0, Elements {'Te': 63.9, 'Ir': 36.1}
	Found duplicates of "Falcondoite", with these properties :
			Density 2.54, Hardness 2.5, Elements {'Mg': 3.24, 'Si': 22.44, 'Ni': 23.45, 'H': 1.88, 'O': 49.0}
			Density 2.54, Hardness 2.5, Elements {'Mg': 3.24, 'Si': 22.44, 'Ni': 23.45, 'H': 1.88, 'O': 49.0}
	Found duplicates of "Gaspeite", with these properties :
			Density 3.71, Hardness 4.5, Elements {'Mg': 6.75, 'Fe': 5.17, 'Ni': 32.58, 'C': 11.11, 'O': 44.4}
			Density 3.71, Hardness 4.5, Elements {'Mg': 6.75, 'Fe': 5.17, 'Ni': 32.58, 'C': 11.11, 'O': 44.4}
	Found duplicates of "Gatehouseite", with these properties :
			Density None, Hardness None, Elements {'Mn': 51.57, 'P': 11.63, 'H': 0.76, 'O': 36.04}
			Density None, Hardness None, Elements {'Mn': 51.57, 'P': 11.63, 'H': 0.76, 'O': 36.04}
	Found duplicates of "Gatelite-Ce", with these properties :
			Density None, Hardness 6.5, Elements {'Ca': 4.13, 'La': 6.5, 'Ce': 18.37, 'Pr': 1.32, 'Sm': 1.41, 'Mg': 1.14, 'Al': 7.83, 'Fe': 1.57, 'Si': 13.42, 'H': 0.15, 'Nd': 10.81, 'O': 32.82, 'F': 0.53}
			Density None, Hardness 6.5, Elements {'Ca': 4.13, 'La': 6.5, 'Ce': 18.37, 'Pr': 1.32, 'Sm': 1.41, 'Mg': 1.14, 'Al': 7.83, 'Fe': 1.57, 'Si': 13.42, 'H': 0.15, 'Nd': 10.81, 'O': 32.82, 'F': 0.53}
	Found duplicates of "Gaultite", with these properties :
			Density 2.52, Hardness 6.0, Elements {'Na': 11.53, 'Zn': 16.4, 'Si': 24.65, 'H': 1.26, 'O': 46.15}
			Density 2.52, Hardness 6.0, Elements {'Na': 11.53, 'Zn': 16.4, 'Si': 24.65, 'H': 1.26, 'O': 46.15}
	Found duplicates of "Geminite", with these properties :
			Density 3.7, Hardness 3.25, Elements {'Cu': 28.69, 'As': 33.83, 'H': 1.37, 'O': 36.12}
			Density 3.7, Hardness 3.25, Elements {'Cu': 28.69, 'As': 33.83, 'H': 1.37, 'O': 36.12}
			Density 3.7, Hardness 3.25, Elements {'Cu': 28.69, 'As': 33.83, 'H': 1.37, 'O': 36.12}
	Found duplicates of "Gengenbachite", with these properties :
			Density None, Hardness None, Elements {'K': 4.38, 'Fe': 18.77, 'P': 20.82, 'H': 2.26, 'O': 53.77}
			Density None, Hardness None, Elements {'K': 4.38, 'Fe': 18.77, 'P': 20.82, 'H': 2.26, 'O': 53.77}
	Found duplicates of "Georgbarsanovite", with these properties :
			Density 3.05, Hardness 5.0, Elements {'K': 0.28, 'Ba': 0.12, 'Na': 8.18, 'Sr': 1.89, 'Ca': 7.66, 'RE': 2.75, 'Y': 0.35, 'Hf': 0.22, 'Zr': 8.94, 'Ti': 0.07, 'Mn': 1.98, 'Nb': 2.59, 'Fe': 4.32, 'Si': 21.51, 'H': 0.05, 'Cl': 1.19, 'O': 37.54, 'F': 0.35}
			Density 3.05, Hardness 5.0, Elements {'K': 0.28, 'Ba': 0.12, 'Na': 8.18, 'Sr': 1.89, 'Ca': 7.66, 'RE': 2.75, 'Y': 0.35, 'Hf': 0.22, 'Zr': 8.94, 'Ti': 0.07, 'Mn': 1.98, 'Nb': 2.59, 'Fe': 4.32, 'Si': 21.51, 'H': 0.05, 'Cl': 1.19, 'O': 37.54, 'F': 0.35}
			Density 3.05, Hardness 5.0, Elements {'K': 0.28, 'Ba': 0.12, 'Na': 8.18, 'Sr': 1.89, 'Ca': 7.66, 'RE': 2.75, 'Y': 0.35, 'Hf': 0.22, 'Zr': 8.94, 'Ti': 0.07, 'Mn': 1.98, 'Nb': 2.59, 'Fe': 4.32, 'Si': 21.51, 'H': 0.05, 'Cl': 1.19, 'O': 37.54, 'F': 0.35}
			Density 3.05, Hardness 5.0, Elements {'K': 0.28, 'Ba': 0.12, 'Na': 8.18, 'Sr': 1.89, 'Ca': 7.66, 'RE': 2.75, 'Y': 0.35, 'Hf': 0.22, 'Zr': 8.94, 'Ti': 0.07, 'Mn': 1.98, 'Nb': 2.59, 'Fe': 4.32, 'Si': 21.51, 'H': 0.05, 'Cl': 1.19, 'O': 37.54, 'F': 0.35}
	Found duplicates of "Georgbokiite", with these properties :
			Density None, Hardness 4.0, Elements {'Cu': 47.1, 'Se': 23.41, 'Cl': 10.51, 'O': 18.97}
			Density None, Hardness 4.0, Elements {'Cu': 47.1, 'Se': 23.41, 'Cl': 10.51, 'O': 18.97}
	Found duplicates of "George-ericksenite", with these properties :
			Density 3.035, Hardness 3.5, Elements {'Na': 8.11, 'Ca': 2.36, 'Mg': 1.43, 'Cr': 6.12, 'H': 1.42, 'I': 44.79, 'O': 35.77}
			Density 3.035, Hardness 3.5, Elements {'Na': 8.11, 'Ca': 2.36, 'Mg': 1.43, 'Cr': 6.12, 'H': 1.42, 'I': 44.79, 'O': 35.77}
			Density 3.035, Hardness 3.5, Elements {'Na': 8.11, 'Ca': 2.36, 'Mg': 1.43, 'Cr': 6.12, 'H': 1.42, 'I': 44.79, 'O': 35.77}
	Found duplicates of "Georgechaoite", with these properties :
			Density 2.7, Hardness 5.0, Elements {'K': 9.36, 'Na': 5.51, 'Zr': 21.85, 'Si': 20.18, 'H': 0.97, 'O': 42.14}
			Density 2.7, Hardness 5.0, Elements {'K': 9.36, 'Na': 5.51, 'Zr': 21.85, 'Si': 20.18, 'H': 0.97, 'O': 42.14}
	Found duplicates of "Gerenite-Y", with these properties :
			Density 3.41, Hardness 5.0, Elements {'Na': 1.32, 'Ca': 6.89, 'RE': 12.38, 'Y': 22.94, 'Si': 19.32, 'H': 0.46, 'O': 36.69}
			Density 3.41, Hardness 5.0, Elements {'Na': 1.32, 'Ca': 6.89, 'RE': 12.38, 'Y': 22.94, 'Si': 19.32, 'H': 0.46, 'O': 36.69}
	Found duplicates of "Germanocolusite", with these properties :
			Density None, Hardness 4.75, Elements {'V': 3.16, 'Cu': 51.32, 'Ge': 10.15, 'As': 3.49, 'S': 31.87}
			Density None, Hardness 4.75, Elements {'V': 3.16, 'Cu': 51.32, 'Ge': 10.15, 'As': 3.49, 'S': 31.87}
	Found duplicates of "Hainite", with these properties :
			Density 3.148, Hardness 5.0, Elements {'Na': 5.53, 'Ca': 23.4, 'RE': 1.98, 'Zr': 5.01, 'Ti': 4.93, 'Mn': 1.89, 'Fe': 0.96, 'Si': 15.04, 'O': 30.44, 'F': 10.83}
			Density 3.148, Hardness 5.0, Elements {'Na': 5.53, 'Ca': 23.4, 'RE': 1.98, 'Zr': 5.01, 'Ti': 4.93, 'Mn': 1.89, 'Fe': 0.96, 'Si': 15.04, 'O': 30.44, 'F': 10.83}
	Found duplicates of "Gibbsite", with these properties :
			Density 2.34, Hardness 2.75, Elements {'Al': 34.59, 'H': 3.88, 'O': 61.53}
			Density 2.34, Hardness 2.75, Elements {'Al': 34.59, 'H': 3.88, 'O': 61.53}
	Found duplicates of "Gillardite", with these properties :
			Density None, Hardness 3.0, Elements {'Fe': 0.05, 'Co': 0.17, 'Cu': 46.32, 'Ni': 12.54, 'H': 1.43, 'Cl': 16.78, 'O': 22.71}
			Density None, Hardness 3.0, Elements {'Fe': 0.05, 'Co': 0.17, 'Cu': 46.32, 'Ni': 12.54, 'H': 1.43, 'Cl': 16.78, 'O': 22.71}
	Found duplicates of "Gillulyite", with these properties :
			Density 4.022, Hardness 2.25, Elements {'Tl': 26.92, 'Sb': 16.03, 'As': 29.6, 'S': 27.45}
			Density 4.022, Hardness 2.25, Elements {'Tl': 26.92, 'Sb': 16.03, 'As': 29.6, 'S': 27.45}
	Found duplicates of "Gilmarite", with these properties :
			Density None, Hardness None, Elements {'Cu': 50.09, 'As': 19.69, 'H': 0.79, 'O': 29.43}
			Density None, Hardness None, Elements {'Cu': 50.09, 'As': 19.69, 'H': 0.79, 'O': 29.43}
	Found duplicates of "Roggianite", with these properties :
			Density 2.02, Hardness None, Elements {'Ca': 14.82, 'Be': 1.67, 'Al': 9.98, 'Si': 20.78, 'H': 1.27, 'O': 51.49}
			Density 2.02, Hardness None, Elements {'Ca': 14.82, 'Be': 1.67, 'Al': 9.98, 'Si': 20.78, 'H': 1.27, 'O': 51.49}
	Found duplicates of "Girvasite", with these properties :
			Density 2.46, Hardness 3.5, Elements {'Na': 3.66, 'Ca': 12.76, 'Mg': 11.61, 'P': 14.79, 'H': 1.77, 'C': 1.91, 'O': 53.5}
			Density 2.46, Hardness 3.5, Elements {'Na': 3.66, 'Ca': 12.76, 'Mg': 11.61, 'P': 14.79, 'H': 1.77, 'C': 1.91, 'O': 53.5}
	Found duplicates of "Gismondine", with these properties :
			Density 2.26, Hardness 4.5, Elements {'Ca': 11.16, 'Al': 15.02, 'Si': 15.63, 'H': 2.52, 'O': 55.67}
			Density 2.26, Hardness 4.5, Elements {'Ca': 11.16, 'Al': 15.02, 'Si': 15.63, 'H': 2.52, 'O': 55.67}
			Density 2.26, Hardness 4.5, Elements {'Ca': 11.16, 'Al': 15.02, 'Si': 15.63, 'H': 2.52, 'O': 55.67}
			Density 2.26, Hardness 4.5, Elements {'Ca': 11.16, 'Al': 15.02, 'Si': 15.63, 'H': 2.52, 'O': 55.67}
	Found duplicates of "Gittinsite", with these properties :
			Density 3.62, Hardness 3.75, Elements {'Ca': 13.38, 'Zr': 30.46, 'Si': 18.76, 'O': 37.4}
			Density 3.62, Hardness 3.75, Elements {'Ca': 13.38, 'Zr': 30.46, 'Si': 18.76, 'O': 37.4}
	Found duplicates of "Gjerdingenite-Ca", with these properties :
			Density 2.79, Hardness 5.0, Elements {'K': 2.99, 'Ba': 0.9, 'Na': 0.85, 'Sr': 2.95, 'Ca': 2.54, 'Ti': 5.98, 'Mn': 0.63, 'Nb': 19.17, 'Al': 0.07, 'Zn': 0.05, 'Fe': 0.14, 'Si': 18.4, 'H': 1.03, 'O': 44.29}
			Density 2.79, Hardness 5.0, Elements {'K': 2.99, 'Ba': 0.9, 'Na': 0.85, 'Sr': 2.95, 'Ca': 2.54, 'Ti': 5.98, 'Mn': 0.63, 'Nb': 19.17, 'Al': 0.07, 'Zn': 0.05, 'Fe': 0.14, 'Si': 18.4, 'H': 1.03, 'O': 44.29}
	Found duplicates of "Gjerdingenite-Fe", with these properties :
			Density 2.82, Hardness 5.0, Elements {'K': 6.3, 'Ti': 3.85, 'Mn': 1.11, 'Nb': 22.44, 'Fe': 3.37, 'Si': 18.09, 'H': 1.05, 'O': 43.79}
			Density 2.82, Hardness 5.0, Elements {'K': 6.3, 'Ti': 3.85, 'Mn': 1.11, 'Nb': 22.44, 'Fe': 3.37, 'Si': 18.09, 'H': 1.05, 'O': 43.79}
	Found duplicates of "Gjerdingenite-Mn", with these properties :
			Density None, Hardness 5.0, Elements {'K': 4.81, 'Ba': 0.61, 'Na': 1.07, 'Mg': 0.03, 'Ti': 4.2, 'Mn': 2.0, 'Nb': 22.05, 'Al': 0.1, 'Zn': 0.42, 'Fe': 1.57, 'Si': 17.91, 'H': 1.1, 'O': 44.13}
			Density None, Hardness 5.0, Elements {'K': 4.81, 'Ba': 0.61, 'Na': 1.07, 'Mg': 0.03, 'Ti': 4.2, 'Mn': 2.0, 'Nb': 22.05, 'Al': 0.1, 'Zn': 0.42, 'Fe': 1.57, 'Si': 17.91, 'H': 1.1, 'O': 44.13}
	Found duplicates of "Gjerdingenite-Na", with these properties :
			Density 2.71, Hardness 5.0, Elements {'K': 3.26, 'Ba': 0.82, 'Na': 2.97, 'Ca': 1.4, 'Ti': 6.07, 'Mn': 0.19, 'Nb': 19.21, 'Al': 0.11, 'Zn': 0.11, 'Fe': 0.43, 'Si': 18.99, 'H': 1.09, 'O': 45.35}
			Density 2.71, Hardness 5.0, Elements {'K': 3.26, 'Ba': 0.82, 'Na': 2.97, 'Ca': 1.4, 'Ti': 6.07, 'Mn': 0.19, 'Nb': 19.21, 'Al': 0.11, 'Zn': 0.11, 'Fe': 0.43, 'Si': 18.99, 'H': 1.09, 'O': 45.35}
	Found duplicates of "Gladiusite", with these properties :
			Density 3.11, Hardness 4.25, Elements {'Mg': 6.72, 'Mn': 0.95, 'Fe': 40.51, 'P': 5.35, 'H': 2.26, 'O': 44.21}
			Density 3.11, Hardness 4.25, Elements {'Mg': 6.72, 'Mn': 0.95, 'Fe': 40.51, 'P': 5.35, 'H': 2.26, 'O': 44.21}
	Found duplicates of "Glagolevite", with these properties :
			Density 2.66, Hardness 3.5, Elements {'Na': 2.95, 'Mg': 22.77, 'Al': 7.15, 'Fe': 0.28, 'Si': 13.84, 'H': 1.65, 'O': 51.36}
			Density 2.66, Hardness 3.5, Elements {'Na': 2.95, 'Mg': 22.77, 'Al': 7.15, 'Fe': 0.28, 'Si': 13.84, 'H': 1.65, 'O': 51.36}
	Found duplicates of "Aphthitalite", with these properties :
			Density 2.7, Hardness 3.0, Elements {'K': 27.46, 'Na': 12.56, 'S': 20.02, 'O': 39.96}
			Density 2.7, Hardness 3.0, Elements {'K': 27.46, 'Na': 12.56, 'S': 20.02, 'O': 39.96}
			Density 2.7, Hardness 3.0, Elements {'K': 27.46, 'Na': 12.56, 'S': 20.02, 'O': 39.96}
			Density 2.7, Hardness 3.0, Elements {'K': 27.46, 'Na': 12.56, 'S': 20.02, 'O': 39.96}
	Found duplicates of "Glauberite", with these properties :
			Density 2.77, Hardness 2.75, Elements {'Na': 16.53, 'Ca': 14.41, 'S': 23.05, 'O': 46.01}
			Density 2.77, Hardness 2.75, Elements {'Na': 16.53, 'Ca': 14.41, 'S': 23.05, 'O': 46.01}
	Found duplicates of "Strengite", with these properties :
			Density 2.87, Hardness 3.5, Elements {'Fe': 29.89, 'P': 16.58, 'H': 2.16, 'O': 51.38}
			Density 2.87, Hardness 3.5, Elements {'Fe': 29.89, 'P': 16.58, 'H': 2.16, 'O': 51.38}
	Found duplicates of "Gmelinite-K", with these properties :
			Density 2.0, Hardness 4.0, Elements {'K': 6.25, 'Na': 1.96, 'Ca': 1.12, 'Al': 9.81, 'Si': 23.62, 'H': 2.15, 'O': 55.09}
			Density 2.0, Hardness 4.0, Elements {'K': 6.25, 'Na': 1.96, 'Ca': 1.12, 'Al': 9.81, 'Si': 23.62, 'H': 2.15, 'O': 55.09}
	Found duplicates of "Gmelinite-Na", with these properties :
			Density 2.09, Hardness 4.5, Elements {'K': 0.31, 'Na': 8.74, 'Ca': 0.06, 'Al': 9.99, 'Si': 23.15, 'H': 2.17, 'O': 55.58}
			Density 2.09, Hardness 4.5, Elements {'K': 0.31, 'Na': 8.74, 'Ca': 0.06, 'Al': 9.99, 'Si': 23.15, 'H': 2.17, 'O': 55.58}
	Found duplicates of "Gorgeyite", with these properties :
			Density 2.95, Hardness 3.5, Elements {'K': 8.96, 'Ca': 22.95, 'H': 0.23, 'S': 22.04, 'O': 45.82}
			Density 2.95, Hardness 3.5, Elements {'K': 8.96, 'Ca': 22.95, 'H': 0.23, 'S': 22.04, 'O': 45.82}
	Found duplicates of "Gotzenite", with these properties :
			Density 3.16, Hardness 5.75, Elements {'Na': 1.58, 'Ca': 30.3, 'Ti': 9.87, 'Al': 1.85, 'Si': 15.44, 'H': 0.14, 'O': 32.99, 'F': 7.83}
			Density 3.16, Hardness 5.75, Elements {'Na': 1.58, 'Ca': 30.3, 'Ti': 9.87, 'Al': 1.85, 'Si': 15.44, 'H': 0.14, 'O': 32.99, 'F': 7.83}
	Found duplicates of "Goldquarryite", with these properties :
			Density 2.78, Hardness 3.5, Elements {'K': 0.16, 'Ca': 0.9, 'Al': 8.05, 'V': 0.05, 'Zn': 0.07, 'Cd': 22.98, 'Cu': 4.29, 'Ni': 0.06, 'P': 12.25, 'H': 2.48, 'O': 45.04, 'F': 3.67}
			Density 2.78, Hardness 3.5, Elements {'K': 0.16, 'Ca': 0.9, 'Al': 8.05, 'V': 0.05, 'Zn': 0.07, 'Cd': 22.98, 'Cu': 4.29, 'Ni': 0.06, 'P': 12.25, 'H': 2.48, 'O': 45.04, 'F': 3.67}
	Found duplicates of "Golyshevite", with these properties :
			Density 2.89, Hardness 5.5, Elements {'K': 0.4, 'Na': 7.09, 'Ca': 8.7, 'La': 0.14, 'Ce': 0.24, 'Zr': 9.26, 'Mn': 0.54, 'Nb': 1.91, 'Al': 0.07, 'Fe': 4.18, 'Si': 23.68, 'H': 0.15, 'C': 0.43, 'Cl': 0.25, 'O': 42.95}
			Density 2.89, Hardness 5.5, Elements {'K': 0.4, 'Na': 7.09, 'Ca': 8.7, 'La': 0.14, 'Ce': 0.24, 'Zr': 9.26, 'Mn': 0.54, 'Nb': 1.91, 'Al': 0.07, 'Fe': 4.18, 'Si': 23.68, 'H': 0.15, 'C': 0.43, 'Cl': 0.25, 'O': 42.95}
	Found duplicates of "Gorceixite", with these properties :
			Density 3.323, Hardness 6.0, Elements {'Ba': 26.86, 'Al': 15.83, 'P': 12.12, 'H': 1.38, 'O': 43.81}
			Density 3.323, Hardness 6.0, Elements {'Ba': 26.86, 'Al': 15.83, 'P': 12.12, 'H': 1.38, 'O': 43.81}
	Found duplicates of "Gordaite", with these properties :
			Density 2.627, Hardness 2.5, Elements {'Na': 3.67, 'Zn': 41.77, 'H': 2.9, 'S': 5.12, 'Cl': 5.66, 'O': 40.88}
			Density 2.627, Hardness 2.5, Elements {'Na': 3.67, 'Zn': 41.77, 'H': 2.9, 'S': 5.12, 'Cl': 5.66, 'O': 40.88}
	Found duplicates of "Gormanite", with these properties :
			Density 3.13, Hardness 4.5, Elements {'Al': 13.6, 'Fe': 21.12, 'P': 15.62, 'H': 1.27, 'O': 48.4}
			Density 3.13, Hardness 4.5, Elements {'Al': 13.6, 'Fe': 21.12, 'P': 15.62, 'H': 1.27, 'O': 48.4}
	Found duplicates of "Gottardiite", with these properties :
			Density 2.14, Hardness None, Elements {'Na': 0.68, 'Ca': 1.97, 'Mg': 0.72, 'Al': 5.04, 'Si': 32.32, 'H': 1.84, 'O': 57.43}
			Density 2.14, Hardness None, Elements {'Na': 0.68, 'Ca': 1.97, 'Mg': 0.72, 'Al': 5.04, 'Si': 32.32, 'H': 1.84, 'O': 57.43}
	Found duplicates of "Gottlobite", with these properties :
			Density 3.49, Hardness 4.5, Elements {'Ca': 19.53, 'Mg': 11.84, 'V': 15.64, 'As': 13.51, 'H': 0.49, 'O': 38.98}
			Density 3.49, Hardness 4.5, Elements {'Ca': 19.53, 'Mg': 11.84, 'V': 15.64, 'As': 13.51, 'H': 0.49, 'O': 38.98}
	Found duplicates of "Goyazite", with these properties :
			Density 3.22, Hardness 4.5, Elements {'Sr': 18.98, 'Al': 17.54, 'P': 13.42, 'H': 1.53, 'O': 48.53}
			Density 3.22, Hardness 4.5, Elements {'Sr': 18.98, 'Al': 17.54, 'P': 13.42, 'H': 1.53, 'O': 48.53}
			Density 3.22, Hardness 4.5, Elements {'Sr': 18.98, 'Al': 17.54, 'P': 13.42, 'H': 1.53, 'O': 48.53}
	Found duplicates of "Graeserite", with these properties :
			Density 4.62, Hardness 5.5, Elements {'Ti': 21.54, 'Fe': 33.49, 'As': 11.23, 'H': 0.15, 'O': 33.58}
			Density 4.62, Hardness 5.5, Elements {'Ti': 21.54, 'Fe': 33.49, 'As': 11.23, 'H': 0.15, 'O': 33.58}
	Found duplicates of "Gramaccioliite-Y", with these properties :
			Density None, Hardness None, Elements {'Ba': 0.15, 'Sr': 1.3, 'Ca': 0.09, 'La': 0.15, 'Ce': 0.62, 'Y': 2.39, 'U': 0.26, 'Ti': 35.55, 'Mn': 1.12, 'Nb': 0.2, 'V': 0.11, 'Zn': 0.79, 'Fe': 16.82, 'Pb': 6.94, 'Nd': 0.16, 'O': 33.36}
			Density None, Hardness None, Elements {'Ba': 0.15, 'Sr': 1.3, 'Ca': 0.09, 'La': 0.15, 'Ce': 0.62, 'Y': 2.39, 'U': 0.26, 'Ti': 35.55, 'Mn': 1.12, 'Nb': 0.2, 'V': 0.11, 'Zn': 0.79, 'Fe': 16.82, 'Pb': 6.94, 'Nd': 0.16, 'O': 33.36}
	Found duplicates of "Grandviewite", with these properties :
			Density None, Hardness None, Elements {'Al': 21.7, 'Cu': 17.04, 'H': 2.61, 'S': 5.73, 'O': 52.91}
			Density None, Hardness None, Elements {'Al': 21.7, 'Cu': 17.04, 'H': 2.61, 'S': 5.73, 'O': 52.91}
	Found duplicates of "Graphite", with these properties :
			Density 2.16, Hardness 1.75, Elements {'C': 100.0}
			Density 2.16, Hardness 1.75, Elements {'C': 100.0}
	Found duplicates of "Grattarolaite", with these properties :
			Density 4.07, Hardness None, Elements {'Fe': 53.96, 'P': 9.98, 'O': 36.07}
			Density 4.07, Hardness None, Elements {'Fe': 53.96, 'P': 9.98, 'O': 36.07}
	Found duplicates of "Graulichite-Ce", with these properties :
			Density 3.9, Hardness None, Elements {'Ba': 3.63, 'Sr': 0.26, 'La': 2.04, 'Ce': 13.79, 'Al': 1.66, 'Fe': 21.98, 'As': 20.8, 'H': 0.95, 'S': 0.05, 'Nd': 1.91, 'O': 32.94}
			Density 3.9, Hardness None, Elements {'Ba': 3.63, 'Sr': 0.26, 'La': 2.04, 'Ce': 13.79, 'Al': 1.66, 'Fe': 21.98, 'As': 20.8, 'H': 0.95, 'S': 0.05, 'Nd': 1.91, 'O': 32.94}
	Found duplicates of "Gravegliaite", with these properties :
			Density 2.39, Hardness None, Elements {'Mn': 42.44, 'H': 2.6, 'S': 13.76, 'O': 41.2}
			Density 2.39, Hardness None, Elements {'Mn': 42.44, 'H': 2.6, 'S': 13.76, 'O': 41.2}
	Found duplicates of "Grenmarite", with these properties :
			Density 3.49, Hardness 4.5, Elements {'Na': 11.82, 'Ca': 1.34, 'Y': 0.23, 'Zr': 24.2, 'Ti': 2.76, 'Mn': 6.62, 'Fe': 1.65, 'Si': 14.4, 'O': 31.58, 'F': 5.41}
			Density 3.49, Hardness 4.5, Elements {'Na': 11.82, 'Ca': 1.34, 'Y': 0.23, 'Zr': 24.2, 'Ti': 2.76, 'Mn': 6.62, 'Fe': 1.65, 'Si': 14.4, 'O': 31.58, 'F': 5.41}
	Found duplicates of "Griceite", with these properties :
			Density 2.62, Hardness 4.5, Elements {'Li': 26.76, 'F': 73.24}
			Density None, Hardness None, Elements {'Li': 26.76, 'F': 73.24}
	Found duplicates of "Saponite", with these properties :
			Density 2.3, Hardness 1.75, Elements {'Na': 0.48, 'Ca': 0.83, 'Mg': 11.39, 'Al': 5.62, 'Fe': 8.72, 'Si': 17.55, 'H': 2.1, 'O': 53.31}
			Density 2.3, Hardness 1.75, Elements {'Na': 0.48, 'Ca': 0.83, 'Mg': 11.39, 'Al': 5.62, 'Fe': 8.72, 'Si': 17.55, 'H': 2.1, 'O': 53.31}
			Density 2.3, Hardness 1.75, Elements {'Na': 0.48, 'Ca': 0.83, 'Mg': 11.39, 'Al': 5.62, 'Fe': 8.72, 'Si': 17.55, 'H': 2.1, 'O': 53.31}
	Found duplicates of "Grossite", with these properties :
			Density 2.88, Hardness None, Elements {'Ca': 15.41, 'Al': 41.51, 'O': 43.08}
			Density 2.88, Hardness None, Elements {'Ca': 15.41, 'Al': 41.51, 'O': 43.08}
	Found duplicates of "Grossular", with these properties :
			Density 3.57, Hardness 7.0, Elements {'Ca': 26.69, 'Al': 11.98, 'Si': 18.71, 'O': 42.62}
			Density 3.57, Hardness 7.0, Elements {'Ca': 26.69, 'Al': 11.98, 'Si': 18.71, 'O': 42.62}
			Density 3.57, Hardness 7.0, Elements {'Ca': 26.69, 'Al': 11.98, 'Si': 18.71, 'O': 42.62}
			Density 3.57, Hardness 7.0, Elements {'Ca': 26.69, 'Al': 11.98, 'Si': 18.71, 'O': 42.62}
	Found duplicates of "Ramsdellite", with these properties :
			Density 4.37, Hardness 3.0, Elements {'Mn': 63.19, 'O': 36.81}
			Density 4.37, Hardness 3.0, Elements {'Mn': 63.19, 'O': 36.81}
	Found duplicates of "Grumiplucite", with these properties :
			Density None, Hardness None, Elements {'Hg': 26.86, 'Bi': 55.97, 'S': 17.17}
			Density None, Hardness None, Elements {'Hg': 26.86, 'Bi': 55.97, 'S': 17.17}
	Found duplicates of "Grunerite", with these properties :
			Density 3.45, Hardness 5.5, Elements {'Fe': 39.03, 'Si': 22.43, 'H': 0.2, 'O': 38.34}
			Density 3.45, Hardness 5.5, Elements {'Fe': 39.03, 'Si': 22.43, 'H': 0.2, 'O': 38.34}
	Found duplicates of "Guanacoite", with these properties :
			Density None, Hardness None, Elements {'Mg': 12.41, 'Cu': 19.39, 'As': 24.45, 'H': 1.97, 'O': 41.77}
			Density None, Hardness None, Elements {'Mg': 12.41, 'Cu': 19.39, 'As': 24.45, 'H': 1.97, 'O': 41.77}
	Found duplicates of "Guarinoite", with these properties :
			Density 2.8, Hardness 1.75, Elements {'Zn': 33.87, 'Co': 7.63, 'Ni': 7.6, 'H': 2.35, 'S': 4.15, 'Cl': 9.18, 'O': 35.22}
			Density 2.8, Hardness 1.75, Elements {'Zn': 33.87, 'Co': 7.63, 'Ni': 7.6, 'H': 2.35, 'S': 4.15, 'Cl': 9.18, 'O': 35.22}
	Found duplicates of "Guettardite", with these properties :
			Density 5.31, Hardness 4.0, Elements {'Sb': 24.95, 'As': 12.56, 'Pb': 38.6, 'S': 23.89}
			Density 5.31, Hardness 4.0, Elements {'Sb': 24.95, 'As': 12.56, 'Pb': 38.6, 'S': 23.89}
	Found duplicates of "Guimaraesite", with these properties :
			Density None, Hardness None, Elements {'Ca': 7.24, 'Mg': 3.84, 'Be': 3.25, 'Zn': 13.28, 'Fe': 5.04, 'P': 16.78, 'H': 1.46, 'O': 49.11}
			Density None, Hardness None, Elements {'Ca': 7.24, 'Mg': 3.84, 'Be': 3.25, 'Zn': 13.28, 'Fe': 5.04, 'P': 16.78, 'H': 1.46, 'O': 49.11}
	Found duplicates of "Baumhauerite", with these properties :
			Density 5.33, Hardness 3.0, Elements {'As': 24.77, 'Pb': 51.38, 'S': 23.85}
			Density 5.33, Hardness 3.0, Elements {'As': 24.77, 'Pb': 51.38, 'S': 23.85}
			Density 5.33, Hardness 3.0, Elements {'As': 24.77, 'Pb': 51.38, 'S': 23.85}
	Found duplicates of "Uraninite", with these properties :
			Density 8.72, Hardness 5.5, Elements {'U': 88.15, 'O': 11.85}
			Density 8.72, Hardness 5.5, Elements {'U': 88.15, 'O': 11.85}
			Density 8.72, Hardness 5.5, Elements {'U': 88.15, 'O': 11.85}
	Found duplicates of "Gutkovaite-Mn", with these properties :
			Density 2.83, Hardness 5.0, Elements {'K': 5.19, 'Ba': 0.88, 'Na': 0.13, 'Sr': 7.47, 'Ca': 3.22, 'Mg': 0.1, 'Zr': 0.07, 'Ti': 13.83, 'Mn': 3.62, 'Nb': 3.36, 'Al': 0.09, 'Zn': 0.11, 'Fe': 0.45, 'Si': 18.07, 'H': 0.91, 'O': 42.49}
			Density 2.83, Hardness 5.0, Elements {'K': 5.19, 'Ba': 0.88, 'Na': 0.13, 'Sr': 7.47, 'Ca': 3.22, 'Mg': 0.1, 'Zr': 0.07, 'Ti': 13.83, 'Mn': 3.62, 'Nb': 3.36, 'Al': 0.09, 'Zn': 0.11, 'Fe': 0.45, 'Si': 18.07, 'H': 0.91, 'O': 42.49}
	Found duplicates of "Gypsum", with these properties :
			Density 2.3, Hardness 2.0, Elements {'Ca': 23.28, 'H': 2.34, 'S': 18.62, 'O': 55.76}
			Density 2.3, Hardness 2.0, Elements {'Ca': 23.28, 'H': 2.34, 'S': 18.62, 'O': 55.76}
			Density 2.3, Hardness 2.0, Elements {'Ca': 23.28, 'H': 2.34, 'S': 18.62, 'O': 55.76}
			Density 2.3, Hardness 2.0, Elements {'Ca': 23.28, 'H': 2.34, 'S': 18.62, 'O': 55.76}
	Found duplicates of "Gyrolite", with these properties :
			Density 2.48, Hardness 2.5, Elements {'Na': 0.64, 'Ca': 17.88, 'Al': 0.75, 'Si': 18.01, 'H': 3.82, 'O': 58.89}
			Density 2.48, Hardness 2.5, Elements {'Na': 0.64, 'Ca': 17.88, 'Al': 0.75, 'Si': 18.01, 'H': 3.82, 'O': 58.89}
	Found duplicates of "Halotrichite", with these properties :
			Density 1.84, Hardness 1.75, Elements {'Al': 6.06, 'Fe': 6.27, 'H': 4.98, 'S': 14.41, 'O': 68.28}
			Density 1.84, Hardness 1.75, Elements {'Al': 6.06, 'Fe': 6.27, 'H': 4.98, 'S': 14.41, 'O': 68.28}
			Density 1.84, Hardness 1.75, Elements {'Al': 6.06, 'Fe': 6.27, 'H': 4.98, 'S': 14.41, 'O': 68.28}
	Found duplicates of "Sodalite", with these properties :
			Density 2.29, Hardness 6.0, Elements {'Na': 18.98, 'Al': 16.7, 'Si': 17.39, 'Cl': 7.32, 'O': 39.62}
			Density 2.29, Hardness 6.0, Elements {'Na': 18.98, 'Al': 16.7, 'Si': 17.39, 'Cl': 7.32, 'O': 39.62}
	Found duplicates of "Haggite", with these properties :
			Density None, Hardness 4.5, Elements {'V': 55.1, 'H': 1.64, 'O': 43.26}
			Density None, Hardness 4.5, Elements {'V': 55.1, 'H': 1.64, 'O': 43.26}
	Found duplicates of "Haggertyite", with these properties :
			Density None, Hardness 5.0, Elements {'Ba': 13.2, 'Mg': 2.34, 'Ti': 23.02, 'Fe': 32.22, 'O': 29.23}
			Density None, Hardness 5.0, Elements {'Ba': 13.2, 'Mg': 2.34, 'Ti': 23.02, 'Fe': 32.22, 'O': 29.23}
	Found duplicates of "Haigerachite", with these properties :
			Density 2.44, Hardness 2.0, Elements {'K': 3.71, 'Fe': 15.92, 'P': 23.54, 'H': 2.11, 'O': 54.72}
			Density 2.44, Hardness 2.0, Elements {'K': 3.71, 'Fe': 15.92, 'P': 23.54, 'H': 2.11, 'O': 54.72}
	Found duplicates of "Haineaultite", with these properties :
			Density None, Hardness 3.5, Elements {'K': 1.78, 'Na': 3.56, 'Ca': 7.28, 'Mg': 0.05, 'Zr': 0.23, 'Ti': 11.56, 'Mn': 0.21, 'Nb': 4.0, 'Fe': 0.39, 'Si': 20.38, 'H': 1.16, 'S': 1.07, 'O': 48.15, 'F': 0.17}
			Density None, Hardness 3.5, Elements {'K': 1.78, 'Na': 3.56, 'Ca': 7.28, 'Mg': 0.05, 'Zr': 0.23, 'Ti': 11.56, 'Mn': 0.21, 'Nb': 4.0, 'Fe': 0.39, 'Si': 20.38, 'H': 1.16, 'S': 1.07, 'O': 48.15, 'F': 0.17}
	Found duplicates of "Haiweeite", with these properties :
			Density 3.1, Hardness 3.5, Elements {'Ca': 4.01, 'U': 47.58, 'Si': 14.03, 'H': 0.81, 'O': 33.58}
			Density 3.1, Hardness 3.5, Elements {'Ca': 4.01, 'U': 47.58, 'Si': 14.03, 'H': 0.81, 'O': 33.58}
	Found duplicates of "Haleniusite-La", with these properties :
			Density None, Hardness None, Elements {'La': 36.53, 'Ce': 35.25, 'Pr': 1.61, 'Nd': 6.6, 'O': 9.15, 'F': 10.86}
			Density None, Hardness None, Elements {'La': 36.53, 'Ce': 35.25, 'Pr': 1.61, 'Nd': 6.6, 'O': 9.15, 'F': 10.86}
	Found duplicates of "Bastnasite-Y", with these properties :
			Density 4.95, Hardness 4.25, Elements {'Y': 52.95, 'C': 7.15, 'O': 28.59, 'F': 11.31}
			Density 4.95, Hardness 4.25, Elements {'Y': 52.95, 'C': 7.15, 'O': 28.59, 'F': 11.31}
	Found duplicates of "Hanawaltite", with these properties :
			Density 9.52, Hardness 4.0, Elements {'Hg': 92.75, 'H': 0.03, 'Cl': 3.51, 'O': 3.7}
			Density 9.52, Hardness 4.0, Elements {'Hg': 92.75, 'H': 0.03, 'Cl': 3.51, 'O': 3.7}
	Found duplicates of "Epidote-Pb", with these properties :
			Density 4.0, Hardness 6.5, Elements {'Sr': 3.0, 'Ca': 8.25, 'Mn': 2.83, 'Al': 9.25, 'Fe': 8.62, 'Si': 14.45, 'H': 0.17, 'Pb': 17.76, 'O': 35.67}
			Density 4.0, Hardness 6.5, Elements {'Sr': 3.0, 'Ca': 8.25, 'Mn': 2.83, 'Al': 9.25, 'Fe': 8.62, 'Si': 14.45, 'H': 0.17, 'Pb': 17.76, 'O': 35.67}
			Density 4.0, Hardness 6.5, Elements {'Sr': 3.0, 'Ca': 8.25, 'Mn': 2.83, 'Al': 9.25, 'Fe': 8.62, 'Si': 14.45, 'H': 0.17, 'Pb': 17.76, 'O': 35.67}
	Found duplicates of "Hapkeite", with these properties :
			Density None, Hardness None, Elements {'Cr': 0.56, 'Fe': 75.73, 'Si': 18.59, 'Ni': 3.13, 'P': 1.98}
			Density None, Hardness None, Elements {'Cr': 0.56, 'Fe': 75.73, 'Si': 18.59, 'Ni': 3.13, 'P': 1.98}
	Found duplicates of "Harrisonite", with these properties :
			Density 4.02, Hardness 4.5, Elements {'Ca': 5.47, 'Mg': 2.32, 'Fe': 41.16, 'Si': 7.67, 'P': 8.45, 'O': 34.93}
			Density 4.02, Hardness 4.5, Elements {'Ca': 5.47, 'Mg': 2.32, 'Fe': 41.16, 'Si': 7.67, 'P': 8.45, 'O': 34.93}
	Found duplicates of "Uranpyrochlore", with these properties :
			Density 4.8, Hardness 5.0, Elements {'Ca': 5.18, 'Ce': 20.71, 'U': 17.59, 'Ta': 16.72, 'Nb': 18.88, 'H': 0.17, 'O': 20.4, 'F': 0.35}
			Density 4.8, Hardness 5.0, Elements {'Ca': 5.18, 'Ce': 20.71, 'U': 17.59, 'Ta': 16.72, 'Nb': 18.88, 'H': 0.17, 'O': 20.4, 'F': 0.35}
	Found duplicates of "Hausmannite", with these properties :
			Density 4.76, Hardness 5.5, Elements {'Mn': 72.03, 'O': 27.97}
			Density 4.76, Hardness 5.5, Elements {'Mn': 72.03, 'O': 27.97}
	Found duplicates of "Hauyne", with these properties :
			Density 2.45, Hardness 5.5, Elements {'Na': 8.91, 'Ca': 7.76, 'Al': 15.68, 'Si': 16.32, 'S': 9.32, 'Cl': 1.72, 'O': 40.29}
			Density 2.45, Hardness 5.5, Elements {'Na': 8.91, 'Ca': 7.76, 'Al': 15.68, 'Si': 16.32, 'S': 9.32, 'Cl': 1.72, 'O': 40.29}
	Found duplicates of "Haycockite", with these properties :
			Density 4.35, Hardness 4.5, Elements {'Fe': 35.35, 'Cu': 32.18, 'S': 32.47}
			Density 4.35, Hardness 4.5, Elements {'Fe': 35.35, 'Cu': 32.18, 'S': 32.47}
	Found duplicates of "Haydeeite", with these properties :
			Density 3.27, Hardness 2.0, Elements {'Mg': 6.04, 'Cu': 49.04, 'H': 1.66, 'Cl': 17.81, 'O': 25.45}
			Density 3.27, Hardness 2.0, Elements {'Mg': 6.04, 'Cu': 49.04, 'H': 1.66, 'Cl': 17.81, 'O': 25.45}
	Found duplicates of "Barite", with these properties :
			Density 4.48, Hardness 3.25, Elements {'Ba': 58.84, 'S': 13.74, 'O': 27.42}
			Density 4.48, Hardness 3.25, Elements {'Ba': 58.84, 'S': 13.74, 'O': 27.42}
			Density 4.48, Hardness 3.25, Elements {'Ba': 58.84, 'S': 13.74, 'O': 27.42}
			Density 4.48, Hardness 3.25, Elements {'Ba': 58.84, 'S': 13.74, 'O': 27.42}
			Density 4.48, Hardness 3.25, Elements {'Ba': 58.84, 'S': 13.74, 'O': 27.42}
	Found duplicates of "Hechtsbergite", with these properties :
			Density 6.87, Hardness 4.5, Elements {'V': 9.0, 'Bi': 73.86, 'H': 0.18, 'O': 16.96}
			Density 6.87, Hardness 4.5, Elements {'V': 9.0, 'Bi': 73.86, 'H': 0.18, 'O': 16.96}
	Found duplicates of "Heftetjernite", with these properties :
			Density None, Hardness None, Elements {'Sc': 15.51, 'Ta': 62.42, 'O': 22.08}
			Density None, Hardness None, Elements {'Sc': 15.51, 'Ta': 62.42, 'O': 22.08}
	Found duplicates of "Hejtmanite", with these properties :
			Density 4.016, Hardness 4.5, Elements {'Ba': 26.68, 'Ti': 9.3, 'Mn': 16.01, 'Fe': 5.43, 'Si': 10.91, 'H': 0.29, 'O': 29.53, 'F': 1.85}
			Density 4.016, Hardness 4.5, Elements {'Ba': 26.68, 'Ti': 9.3, 'Mn': 16.01, 'Fe': 5.43, 'Si': 10.91, 'H': 0.29, 'O': 29.53, 'F': 1.85}
	Found duplicates of "Hellandite-Y", with these properties :
			Density 3.7, Hardness 5.75, Elements {'Ca': 11.68, 'Ce': 6.81, 'RE': 13.99, 'Y': 12.95, 'Al': 1.97, 'Si': 10.91, 'B': 4.2, 'H': 0.2, 'O': 37.3}
			Density 3.7, Hardness 5.75, Elements {'Ca': 11.68, 'Ce': 6.81, 'RE': 13.99, 'Y': 12.95, 'Al': 1.97, 'Si': 10.91, 'B': 4.2, 'H': 0.2, 'O': 37.3}
	Found duplicates of "Bassanite", with these properties :
			Density 2.7, Hardness None, Elements {'Ca': 27.61, 'H': 0.69, 'S': 22.09, 'O': 49.6}
			Density 2.7, Hardness None, Elements {'Ca': 27.61, 'H': 0.69, 'S': 22.09, 'O': 49.6}
	Found duplicates of "Hemloite", with these properties :
			Density None, Hardness 6.5, Elements {'Ti': 27.35, 'Al': 2.57, 'V': 14.55, 'Fe': 10.63, 'Sb': 2.9, 'As': 5.35, 'H': 0.1, 'O': 36.56}
			Density None, Hardness 6.5, Elements {'Ti': 27.35, 'Al': 2.57, 'V': 14.55, 'Fe': 10.63, 'Sb': 2.9, 'As': 5.35, 'H': 0.1, 'O': 36.56}
	Found duplicates of "Hennomartinite", with these properties :
			Density None, Hardness 4.0, Elements {'Sr': 20.98, 'Mn': 26.31, 'Si': 13.45, 'H': 0.97, 'O': 38.3}
			Density None, Hardness 4.0, Elements {'Sr': 20.98, 'Mn': 26.31, 'Si': 13.45, 'H': 0.97, 'O': 38.3}
	Found duplicates of "Henrymeyerite", with these properties :
			Density 4.0, Hardness 5.5, Elements {'Ba': 17.51, 'Ti': 42.73, 'Fe': 7.12, 'O': 32.64}
			Density 4.0, Hardness 5.5, Elements {'Ba': 17.51, 'Ti': 42.73, 'Fe': 7.12, 'O': 32.64}
	Found duplicates of "Hephaistosite", with these properties :
			Density None, Hardness None, Elements {'Tl': 24.23, 'Pb': 52.51, 'Br': 1.41, 'Cl': 21.68, 'F': 0.17}
			Density None, Hardness None, Elements {'Tl': 24.23, 'Pb': 52.51, 'Br': 1.41, 'Cl': 21.68, 'F': 0.17}
	Found duplicates of "Herbertsmithite", with these properties :
			Density 3.85, Hardness 3.25, Elements {'Zn': 14.59, 'Cu': 44.75, 'H': 1.39, 'Cl': 17.14, 'O': 22.13}
			Density 3.85, Hardness 3.25, Elements {'Zn': 14.59, 'Cu': 44.75, 'H': 1.39, 'Cl': 17.14, 'O': 22.13}
	Found duplicates of "Devilline", with these properties :
			Density 3.11, Hardness 2.5, Elements {'Ca': 6.24, 'Cu': 39.56, 'H': 1.88, 'S': 9.98, 'O': 42.33}
			Density 3.11, Hardness 2.5, Elements {'Ca': 6.24, 'Cu': 39.56, 'H': 1.88, 'S': 9.98, 'O': 42.33}
	Found duplicates of "Hessite", with these properties :
			Density 7.55, Hardness 1.75, Elements {'Ag': 62.84, 'Te': 37.16}
			Density 7.55, Hardness 1.75, Elements {'Ag': 62.84, 'Te': 37.16}
	Found duplicates of "Heulandite-Ba", with these properties :
			Density 2.35, Hardness 3.5, Elements {'K': 0.48, 'Ba': 11.43, 'Na': 0.25, 'Sr': 0.88, 'Ca': 1.89, 'Al': 8.08, 'Si': 25.36, 'H': 1.47, 'O': 50.15}
			Density 2.35, Hardness 3.5, Elements {'K': 0.48, 'Ba': 11.43, 'Na': 0.25, 'Sr': 0.88, 'Ca': 1.89, 'Al': 8.08, 'Si': 25.36, 'H': 1.47, 'O': 50.15}
	Found duplicates of "Heulandite-Ca", with these properties :
			Density 2.2, Hardness 3.25, Elements {'K': 0.6, 'Ba': 0.29, 'Na': 1.03, 'Sr': 0.16, 'Ca': 5.06, 'Mg': 0.01, 'Al': 8.95, 'Si': 26.54, 'H': 1.86, 'O': 55.51}
			Density 2.2, Hardness 3.25, Elements {'K': 0.6, 'Ba': 0.29, 'Na': 1.03, 'Sr': 0.16, 'Ca': 5.06, 'Mg': 0.01, 'Al': 8.95, 'Si': 26.54, 'H': 1.86, 'O': 55.51}
	Found duplicates of "Heulandite-Sr", with these properties :
			Density 2.2, Hardness 3.25, Elements {'K': 0.3, 'Ba': 0.67, 'Na': 0.32, 'Sr': 6.39, 'Ca': 2.45, 'Mg': 0.02, 'Al': 8.61, 'Si': 26.26, 'H': 1.68, 'O': 53.31}
			Density 2.2, Hardness 3.25, Elements {'K': 0.3, 'Ba': 0.67, 'Na': 0.32, 'Sr': 6.39, 'Ca': 2.45, 'Mg': 0.02, 'Al': 8.61, 'Si': 26.26, 'H': 1.68, 'O': 53.31}
	Found duplicates of "Hexaferrum", with these properties :
			Density None, Hardness 6.5, Elements {'Fe': 41.96, 'Ir': 31.11, 'Os': 17.59, 'Ru': 9.35}
			Density None, Hardness 6.5, Elements {'Fe': 41.96, 'Ir': 31.11, 'Os': 17.59, 'Ru': 9.35}
	Found duplicates of "Hexamolybdenum", with these properties :
			Density None, Hardness None, Elements {'Fe': 4.29, 'Ni': 0.56, 'Mo': 51.64, 'Ir': 12.93, 'Os': 5.48, 'Ru': 23.32, 'W': 1.77}
			Density None, Hardness None, Elements {'Fe': 4.29, 'Ni': 0.56, 'Mo': 51.64, 'Ir': 12.93, 'Os': 5.48, 'Ru': 23.32, 'W': 1.77}
	Found duplicates of "Hiarneite", with these properties :
			Density 5.44, Hardness 7.0, Elements {'Na': 0.5, 'Ca': 5.25, 'Zr': 29.85, 'Ti': 3.13, 'Mn': 15.58, 'Fe': 1.83, 'Sb': 15.94, 'O': 27.92}
			Density 5.44, Hardness 7.0, Elements {'Na': 0.5, 'Ca': 5.25, 'Zr': 29.85, 'Ti': 3.13, 'Mn': 15.58, 'Fe': 1.83, 'Sb': 15.94, 'O': 27.92}
	Found duplicates of "Hibbingite", with these properties :
			Density 3.04, Hardness 3.5, Elements {'Mg': 6.66, 'Fe': 45.93, 'H': 1.66, 'Cl': 19.44, 'O': 26.32}
			Density 3.04, Hardness 3.5, Elements {'Mg': 6.66, 'Fe': 45.93, 'H': 1.66, 'Cl': 19.44, 'O': 26.32}
	Found duplicates of "Hibschite", with these properties :
			Density 3.13, Hardness 6.5, Elements {'Ca': 28.2, 'Al': 12.66, 'Si': 13.17, 'H': 0.95, 'O': 45.03}
			Density 3.13, Hardness 6.5, Elements {'Ca': 28.2, 'Al': 12.66, 'Si': 13.17, 'H': 0.95, 'O': 45.03}
	Found duplicates of "Conichalcite", with these properties :
			Density 4.1, Hardness 4.5, Elements {'Ca': 15.44, 'Cu': 24.48, 'As': 28.87, 'H': 0.39, 'O': 30.82}
			Density 4.1, Hardness 4.5, Elements {'Ca': 15.44, 'Cu': 24.48, 'As': 28.87, 'H': 0.39, 'O': 30.82}
	Found duplicates of "Hilairite", with these properties :
			Density 2.72, Hardness 4.5, Elements {'Na': 10.96, 'Zr': 21.75, 'Si': 20.08, 'H': 1.44, 'O': 45.77}
			Density 2.72, Hardness 4.5, Elements {'Na': 10.96, 'Zr': 21.75, 'Si': 20.08, 'H': 1.44, 'O': 45.77}
	Found duplicates of "Hillite", with these properties :
			Density 3.16, Hardness 3.5, Elements {'Na': 0.06, 'Ca': 21.46, 'Mg': 2.59, 'Zn': 11.73, 'P': 17.63, 'H': 1.13, 'O': 45.39}
			Density 3.16, Hardness 3.5, Elements {'Na': 0.06, 'Ca': 21.46, 'Mg': 2.59, 'Zn': 11.73, 'P': 17.63, 'H': 1.13, 'O': 45.39}
	Found duplicates of "Hingganite-Ce", with these properties :
			Density None, Hardness 5.5, Elements {'Ca': 5.22, 'La': 9.65, 'Ce': 24.94, 'Pr': 1.84, 'Sm': 0.33, 'Gd': 0.07, 'Dy': 0.04, 'Y': 0.58, 'Be': 3.95, 'Fe': 2.91, 'Si': 12.32, 'H': 0.33, 'Nd': 4.07, 'O': 33.76}
			Density None, Hardness 5.5, Elements {'Ca': 5.22, 'La': 9.65, 'Ce': 24.94, 'Pr': 1.84, 'Sm': 0.33, 'Gd': 0.07, 'Dy': 0.04, 'Y': 0.58, 'Be': 3.95, 'Fe': 2.91, 'Si': 12.32, 'H': 0.33, 'Nd': 4.07, 'O': 33.76}
	Found duplicates of "Hingganite-Y", with these properties :
			Density None, Hardness 5.5, Elements {'Ca': 2.59, 'La': 0.32, 'Ce': 1.29, 'Pr': 0.32, 'Sm': 1.04, 'Gd': 2.18, 'Dy': 2.25, 'Y': 24.81, 'Ho': 1.14, 'Er': 1.54, 'Tm': 0.39, 'Lu': 0.81, 'Tb': 0.37, 'Yb': 2.0, 'Be': 4.3, 'Fe': 2.96, 'Si': 13.41, 'H': 0.36, 'Nd': 2.0, 'O': 35.94}
			Density None, Hardness 5.5, Elements {'Ca': 2.59, 'La': 0.32, 'Ce': 1.29, 'Pr': 0.32, 'Sm': 1.04, 'Gd': 2.18, 'Dy': 2.25, 'Y': 24.81, 'Ho': 1.14, 'Er': 1.54, 'Tm': 0.39, 'Lu': 0.81, 'Tb': 0.37, 'Yb': 2.0, 'Be': 4.3, 'Fe': 2.96, 'Si': 13.41, 'H': 0.36, 'Nd': 2.0, 'O': 35.94}
			Density None, Hardness 5.5, Elements {'Ca': 2.59, 'La': 0.32, 'Ce': 1.29, 'Pr': 0.32, 'Sm': 1.04, 'Gd': 2.18, 'Dy': 2.25, 'Y': 24.81, 'Ho': 1.14, 'Er': 1.54, 'Tm': 0.39, 'Lu': 0.81, 'Tb': 0.37, 'Yb': 2.0, 'Be': 4.3, 'Fe': 2.96, 'Si': 13.41, 'H': 0.36, 'Nd': 2.0, 'O': 35.94}
			Density None, Hardness 5.5, Elements {'Ca': 2.59, 'La': 0.32, 'Ce': 1.29, 'Pr': 0.32, 'Sm': 1.04, 'Gd': 2.18, 'Dy': 2.25, 'Y': 24.81, 'Ho': 1.14, 'Er': 1.54, 'Tm': 0.39, 'Lu': 0.81, 'Tb': 0.37, 'Yb': 2.0, 'Be': 4.3, 'Fe': 2.96, 'Si': 13.41, 'H': 0.36, 'Nd': 2.0, 'O': 35.94}
	Found duplicates of "Hisingerite", with these properties :
			Density 2.54, Hardness 3.0, Elements {'Fe': 31.74, 'Si': 15.96, 'H': 2.29, 'O': 50.01}
			Density 2.54, Hardness 3.0, Elements {'Fe': 31.74, 'Si': 15.96, 'H': 2.29, 'O': 50.01}
			Density 2.54, Hardness 3.0, Elements {'Fe': 31.74, 'Si': 15.96, 'H': 2.29, 'O': 50.01}
	Found duplicates of "Plumbogummite", with these properties :
			Density 4.5, Hardness 4.5, Elements {'Al': 13.93, 'P': 10.66, 'H': 1.21, 'Pb': 35.65, 'O': 38.54}
			Density 4.5, Hardness 4.5, Elements {'Al': 13.93, 'P': 10.66, 'H': 1.21, 'Pb': 35.65, 'O': 38.54}
			Density 4.5, Hardness 4.5, Elements {'Al': 13.93, 'P': 10.66, 'H': 1.21, 'Pb': 35.65, 'O': 38.54}
	Found duplicates of "Hochelagaite", with these properties :
			Density 2.89, Hardness 4.0, Elements {'Na': 0.78, 'Sr': 1.79, 'Ca': 3.27, 'Nb': 50.58, 'H': 2.2, 'O': 41.38}
			Density 2.89, Hardness 4.0, Elements {'Na': 0.78, 'Sr': 1.79, 'Ca': 3.27, 'Nb': 50.58, 'H': 2.2, 'O': 41.38}
	Found duplicates of "Hogtuvaite", with these properties :
			Density 3.85, Hardness 5.5, Elements {'Na': 1.37, 'Ca': 7.18, 'Mg': 1.45, 'Ti': 5.15, 'Mn': 0.66, 'Be': 1.94, 'Al': 1.93, 'Fe': 30.02, 'Si': 12.08, 'O': 38.22}
			Density 3.85, Hardness 5.5, Elements {'Na': 1.37, 'Ca': 7.18, 'Mg': 1.45, 'Ti': 5.15, 'Mn': 0.66, 'Be': 1.94, 'Al': 1.93, 'Fe': 30.02, 'Si': 12.08, 'O': 38.22}
			Density 3.85, Hardness 5.5, Elements {'Na': 1.37, 'Ca': 7.18, 'Mg': 1.45, 'Ti': 5.15, 'Mn': 0.66, 'Be': 1.94, 'Al': 1.93, 'Fe': 30.02, 'Si': 12.08, 'O': 38.22}
	Found duplicates of "Hoelite", with these properties :
			Density 1.42, Hardness None, Elements {'H': 3.87, 'C': 80.76, 'O': 15.37}
			Density 1.42, Hardness None, Elements {'H': 3.87, 'C': 80.76, 'O': 15.37}
	Found duplicates of "Hornesite", with these properties :
			Density 2.57, Hardness 1.0, Elements {'Mg': 14.73, 'As': 30.28, 'H': 3.26, 'O': 51.73}
			Density 2.57, Hardness 1.0, Elements {'Mg': 14.73, 'As': 30.28, 'H': 3.26, 'O': 51.73}
	Found duplicates of "Hoganite", with these properties :
			Density None, Hardness 1.5, Elements {'Fe': 0.28, 'Cu': 31.56, 'H': 3.95, 'C': 23.92, 'O': 40.29}
			Density None, Hardness 1.5, Elements {'Fe': 0.28, 'Cu': 31.56, 'H': 3.95, 'C': 23.92, 'O': 40.29}
	Found duplicates of "Hohmannite", with these properties :
			Density 2.2, Hardness 3.0, Elements {'Fe': 24.07, 'H': 3.48, 'S': 13.82, 'O': 58.63}
			Density 2.2, Hardness 3.0, Elements {'Fe': 24.07, 'H': 3.48, 'S': 13.82, 'O': 58.63}
	Found duplicates of "Holfertite", with these properties :
			Density None, Hardness 4.0, Elements {'K': 0.24, 'Ca': 1.67, 'U': 63.79, 'Ti': 7.15, 'Fe': 0.34, 'H': 0.93, 'O': 25.87}
			Density None, Hardness 4.0, Elements {'K': 0.24, 'Ca': 1.67, 'U': 63.79, 'Ti': 7.15, 'Fe': 0.34, 'H': 0.93, 'O': 25.87}
	Found duplicates of "Holtstamite", with these properties :
			Density None, Hardness 6.0, Elements {'Ca': 26.4, 'Mn': 8.2, 'Al': 5.63, 'Fe': 4.54, 'Si': 12.33, 'H': 0.88, 'O': 42.02}
			Density None, Hardness 6.0, Elements {'Ca': 26.4, 'Mn': 8.2, 'Al': 5.63, 'Fe': 4.54, 'Si': 12.33, 'H': 0.88, 'O': 42.02}
	Found duplicates of "Horvathite-Y", with these properties :
			Density 3.58, Hardness 4.0, Elements {'Na': 10.95, 'Y': 42.36, 'C': 5.72, 'O': 22.87, 'F': 18.1}
			Density 3.58, Hardness 4.0, Elements {'Na': 10.95, 'Y': 42.36, 'C': 5.72, 'O': 22.87, 'F': 18.1}
	Found duplicates of "Huangite", with these properties :
			Density None, Hardness 3.5, Elements {'Ca': 5.07, 'Al': 20.48, 'H': 1.53, 'S': 16.23, 'O': 56.68}
			Density None, Hardness 3.5, Elements {'Ca': 5.07, 'Al': 20.48, 'H': 1.53, 'S': 16.23, 'O': 56.68}
	Found duplicates of "Hubeite", with these properties :
			Density 3.02, Hardness 5.5, Elements {'Ca': 15.61, 'Mn': 8.78, 'Al': 0.2, 'Fe': 9.75, 'Si': 20.64, 'H': 0.93, 'O': 44.09}
			Density 3.02, Hardness 5.5, Elements {'Ca': 15.61, 'Mn': 8.78, 'Al': 0.2, 'Fe': 9.75, 'Si': 20.64, 'H': 0.93, 'O': 44.09}
	Found duplicates of "Hubnerite", with these properties :
			Density 7.15, Hardness 4.5, Elements {'Mn': 18.14, 'W': 60.72, 'O': 21.14}
			Density 7.15, Hardness 4.5, Elements {'Mn': 18.14, 'W': 60.72, 'O': 21.14}
	Found duplicates of "Hugelite", with these properties :
			Density 5.1, Hardness 2.5, Elements {'U': 43.96, 'As': 9.22, 'H': 0.62, 'Pb': 25.51, 'O': 20.68}
			Density 5.1, Hardness 2.5, Elements {'U': 43.96, 'As': 9.22, 'H': 0.62, 'Pb': 25.51, 'O': 20.68}
	Found duplicates of "Alluaudite", with these properties :
			Density 3.51, Hardness 5.25, Elements {'Na': 2.86, 'Ca': 0.83, 'Mg': 0.5, 'Mn': 13.66, 'Fe': 23.14, 'P': 19.25, 'O': 39.77}
			Density 3.51, Hardness 5.25, Elements {'Na': 2.86, 'Ca': 0.83, 'Mg': 0.5, 'Mn': 13.66, 'Fe': 23.14, 'P': 19.25, 'O': 39.77}
	Found duplicates of "Hunchunite", with these properties :
			Density 16.0, Hardness 3.5, Elements {'Ag': 9.69, 'Pb': 37.23, 'Au': 53.08}
			Density 16.0, Hardness 3.5, Elements {'Ag': 9.69, 'Pb': 37.23, 'Au': 53.08}
	Found duplicates of "Hundholmenite-Y", with these properties :
			Density None, Hardness 5.5, Elements {'Na': 0.91, 'Ca': 5.56, 'RE': 28.56, 'Y': 21.16, 'Al': 0.8, 'Fe': 0.55, 'Si': 7.52, 'B': 1.29, 'As': 2.23, 'O': 25.39, 'F': 6.03}
			Density None, Hardness 5.5, Elements {'Na': 0.91, 'Ca': 5.56, 'RE': 28.56, 'Y': 21.16, 'Al': 0.8, 'Fe': 0.55, 'Si': 7.52, 'B': 1.29, 'As': 2.23, 'O': 25.39, 'F': 6.03}
	Found duplicates of "Hureaulite", with these properties :
			Density 3.18, Hardness 5.0, Elements {'Mn': 37.7, 'P': 17.0, 'H': 1.38, 'O': 43.92}
			Density 3.18, Hardness 5.0, Elements {'Mn': 37.7, 'P': 17.0, 'H': 1.38, 'O': 43.92}
	Found duplicates of "Hydrobasaluminite", with these properties :
			Density 2.275, Hardness None, Elements {'Al': 16.75, 'H': 6.26, 'S': 4.98, 'O': 72.01}
			Density 2.275, Hardness None, Elements {'Al': 16.75, 'H': 6.26, 'S': 4.98, 'O': 72.01}
	Found duplicates of "Chalcocyanite", with these properties :
			Density 3.65, Hardness 3.5, Elements {'Cu': 39.81, 'S': 20.09, 'O': 40.1}
			Density 3.65, Hardness 3.5, Elements {'Cu': 39.81, 'S': 20.09, 'O': 40.1}
			Density 3.65, Hardness 3.5, Elements {'Cu': 39.81, 'S': 20.09, 'O': 40.1}
	Found duplicates of "Hydrodresserite", with these properties :
			Density 2.8, Hardness 3.5, Elements {'Ba': 31.69, 'Al': 12.45, 'H': 2.33, 'C': 5.54, 'O': 47.99}
			Density 2.8, Hardness 3.5, Elements {'Ba': 31.69, 'Al': 12.45, 'H': 2.33, 'C': 5.54, 'O': 47.99}
	Found duplicates of "Chalcophanite", with these properties :
			Density 3.91, Hardness 2.5, Elements {'Mn': 40.68, 'Zn': 17.09, 'Fe': 6.08, 'H': 1.32, 'O': 34.84}
			Density 3.91, Hardness 2.5, Elements {'Mn': 40.68, 'Zn': 17.09, 'Fe': 6.08, 'H': 1.32, 'O': 34.84}
	Found duplicates of "Chernikovite", with these properties :
			Density None, Hardness 2.25, Elements {'U': 54.34, 'P': 7.07, 'H': 2.07, 'O': 36.52}
			Density None, Hardness 2.25, Elements {'U': 54.34, 'P': 7.07, 'H': 2.07, 'O': 36.52}
	Found duplicates of "Hydroniumjarosite", with these properties :
			Density 2.7, Hardness 4.25, Elements {'Fe': 34.85, 'H': 1.89, 'S': 13.34, 'O': 49.92}
			Density 2.7, Hardness 4.25, Elements {'Fe': 34.85, 'H': 1.89, 'S': 13.34, 'O': 49.92}
			Density 2.7, Hardness 4.25, Elements {'Fe': 34.85, 'H': 1.89, 'S': 13.34, 'O': 49.92}
			Density 2.7, Hardness 4.25, Elements {'Fe': 34.85, 'H': 1.89, 'S': 13.34, 'O': 49.92}
	Found duplicates of "Brammallite", with these properties :
			Density 2.85, Hardness 2.75, Elements {'Na': 3.87, 'Mg': 3.72, 'Al': 13.77, 'Fe': 1.43, 'Si': 21.5, 'H': 1.28, 'O': 54.43}
			Density 2.85, Hardness 2.75, Elements {'Na': 3.87, 'Mg': 3.72, 'Al': 13.77, 'Fe': 1.43, 'Si': 21.5, 'H': 1.28, 'O': 54.43}
			Density 2.85, Hardness 2.75, Elements {'Na': 3.87, 'Mg': 3.72, 'Al': 13.77, 'Fe': 1.43, 'Si': 21.5, 'H': 1.28, 'O': 54.43}
			Density 2.85, Hardness 2.75, Elements {'Na': 3.87, 'Mg': 3.72, 'Al': 13.77, 'Fe': 1.43, 'Si': 21.5, 'H': 1.28, 'O': 54.43}
	Found duplicates of "Hydroromarchite", with these properties :
			Density None, Hardness None, Elements {'Sn': 84.36, 'H': 0.48, 'O': 15.16}
			Density None, Hardness None, Elements {'Sn': 84.36, 'H': 0.48, 'O': 15.16}
	Found duplicates of "Hydrowoodwardite", with these properties :
			Density 2.33, Hardness None, Elements {'Al': 11.55, 'Cu': 27.2, 'H': 3.02, 'S': 6.86, 'O': 51.37}
			Density 2.33, Hardness None, Elements {'Al': 11.55, 'Cu': 27.2, 'H': 3.02, 'S': 6.86, 'O': 51.37}
	Found duplicates of "Apatite-CaOH", with these properties :
			Density 3.08, Hardness 5.0, Elements {'Ca': 39.89, 'P': 18.5, 'H': 0.2, 'O': 41.41}
			Density 3.08, Hardness 5.0, Elements {'Ca': 39.89, 'P': 18.5, 'H': 0.2, 'O': 41.41}
			Density 3.08, Hardness 5.0, Elements {'Ca': 39.89, 'P': 18.5, 'H': 0.2, 'O': 41.41}
	Found duplicates of "Apophyllite-KOH", with these properties :
			Density 2.34, Hardness 4.5, Elements {'K': 4.38, 'Na': 0.45, 'Ca': 21.12, 'Si': 29.6, 'H': 0.11, 'O': 43.84, 'F': 0.5}
			Density 2.34, Hardness 4.5, Elements {'K': 4.38, 'Na': 0.45, 'Ca': 21.12, 'Si': 29.6, 'H': 0.11, 'O': 43.84, 'F': 0.5}
	Found duplicates of "Hydroxycancrinite", with these properties :
			Density 2.32, Hardness 6.0, Elements {'Na': 18.99, 'Al': 16.72, 'Si': 17.4, 'H': 0.62, 'O': 46.26}
			Density 2.32, Hardness 6.0, Elements {'Na': 18.99, 'Al': 16.72, 'Si': 17.4, 'H': 0.62, 'O': 46.26}
	Found duplicates of "Hydroxylbastnasite-Ce", with these properties :
			Density 4.745, Hardness 4.0, Elements {'Ce': 64.53, 'H': 0.46, 'C': 5.53, 'O': 29.47}
			Density 4.745, Hardness 4.0, Elements {'Ce': 64.53, 'H': 0.46, 'C': 5.53, 'O': 29.47}
	Found duplicates of "Hydroxylbastnasite-La", with these properties :
			Density 4.75, Hardness 4.0, Elements {'La': 64.33, 'H': 0.47, 'C': 5.56, 'O': 29.64}
			Density 4.75, Hardness 4.0, Elements {'La': 64.33, 'H': 0.47, 'C': 5.56, 'O': 29.64}
			Density 4.75, Hardness 4.0, Elements {'La': 64.33, 'H': 0.47, 'C': 5.56, 'O': 29.64}
	Found duplicates of "Hydroxylbastnasite-Nd", with these properties :
			Density None, Hardness 1.5, Elements {'H': 0.46, 'C': 5.43, 'Nd': 65.19, 'O': 28.92}
			Density None, Hardness 1.5, Elements {'H': 0.46, 'C': 5.43, 'Nd': 65.19, 'O': 28.92}
			Density None, Hardness 1.5, Elements {'H': 0.46, 'C': 5.43, 'Nd': 65.19, 'O': 28.92}
	Found duplicates of "Hydroxylherderite", with these properties :
			Density 2.94, Hardness 5.25, Elements {'Ca': 24.88, 'Be': 5.6, 'P': 19.23, 'H': 0.63, 'O': 49.67}
			Density 2.94, Hardness 5.25, Elements {'Ca': 24.88, 'Be': 5.6, 'P': 19.23, 'H': 0.63, 'O': 49.67}
	Found duplicates of "Hydroxylpyromorphite", with these properties :
			Density None, Hardness None, Elements {'P': 6.95, 'H': 0.08, 'Pb': 77.43, 'O': 15.55}
			Density None, Hardness None, Elements {'P': 6.95, 'H': 0.08, 'Pb': 77.43, 'O': 15.55}
	Found duplicates of "Hydroxylborite", with these properties :
			Density 2.89, Hardness 3.5, Elements {'Mg': 39.75, 'B': 5.72, 'H': 1.09, 'O': 43.18, 'F': 10.26}
			Density 2.89, Hardness 3.5, Elements {'Mg': 39.75, 'B': 5.72, 'H': 1.09, 'O': 43.18, 'F': 10.26}
	Found duplicates of "Hydroxylclinohumite", with these properties :
			Density 3.13, Hardness 6.5, Elements {'Mg': 35.2, 'Si': 18.08, 'H': 0.29, 'O': 45.82, 'F': 0.61}
			Density 3.13, Hardness 6.5, Elements {'Mg': 35.2, 'Si': 18.08, 'H': 0.29, 'O': 45.82, 'F': 0.61}
	Found duplicates of "Ellestadite-OH", with these properties :
			Density 3.02, Hardness 4.5, Elements {'Ca': 39.65, 'Si': 8.34, 'H': 0.12, 'S': 9.52, 'Cl': 2.1, 'O': 39.89, 'F': 0.38}
			Density 3.02, Hardness 4.5, Elements {'Ca': 39.65, 'Si': 8.34, 'H': 0.12, 'S': 9.52, 'Cl': 2.1, 'O': 39.89, 'F': 0.38}
	Found duplicates of "Hydroxylwagnerite", with these properties :
			Density None, Hardness None, Elements {'Mg': 30.27, 'P': 19.29, 'H': 0.63, 'O': 49.81}
			Density None, Hardness None, Elements {'Mg': 30.27, 'P': 19.29, 'H': 0.63, 'O': 49.81}
	Found duplicates of "Hydroxyuvite", with these properties :
			Density None, Hardness None, Elements {'Ca': 4.12, 'Mg': 9.99, 'Al': 13.86, 'Si': 17.32, 'B': 3.33, 'H': 0.41, 'O': 50.97}
			Density None, Hardness None, Elements {'Ca': 4.12, 'Mg': 9.99, 'Al': 13.86, 'Si': 17.32, 'B': 3.33, 'H': 0.41, 'O': 50.97}
	Found duplicates of "Hyttsjoite", with these properties :
			Density 5.09, Hardness None, Elements {'Ba': 4.01, 'Ca': 2.92, 'Mn': 1.6, 'Fe': 1.63, 'Si': 12.3, 'H': 0.18, 'Pb': 54.43, 'Cl': 0.52, 'O': 22.42}
			Density 5.09, Hardness None, Elements {'Ba': 4.01, 'Ca': 2.92, 'Mn': 1.6, 'Fe': 1.63, 'Si': 12.3, 'H': 0.18, 'Pb': 54.43, 'Cl': 0.52, 'O': 22.42}
	Found duplicates of "Aplowite", with these properties :
			Density 2.33, Hardness 3.0, Elements {'Mn': 7.3, 'Co': 15.66, 'Ni': 2.6, 'H': 3.57, 'S': 14.2, 'O': 56.68}
			Density 2.33, Hardness 3.0, Elements {'Mn': 7.3, 'Co': 15.66, 'Ni': 2.6, 'H': 3.57, 'S': 14.2, 'O': 56.68}
	Found duplicates of "Berryite", with these properties :
			Density 6.7, Hardness 3.5, Elements {'Cu': 6.18, 'Ag': 6.87, 'Bi': 49.0, 'Pb': 20.89, 'S': 17.06}
			Density 6.7, Hardness 3.5, Elements {'Cu': 6.18, 'Ag': 6.87, 'Bi': 49.0, 'Pb': 20.89, 'S': 17.06}
	Found duplicates of "Veenite", with these properties :
			Density 5.92, Hardness 3.75, Elements {'Sb': 17.26, 'As': 8.69, 'Pb': 53.4, 'S': 20.66}
			Density 5.92, Hardness 3.75, Elements {'Sb': 17.26, 'As': 8.69, 'Pb': 53.4, 'S': 20.66}
	Found duplicates of "Twinnite", with these properties :
			Density 5.26, Hardness 3.5, Elements {'Sb': 40.28, 'As': 1.3, 'Pb': 36.08, 'S': 22.33}
			Density 5.26, Hardness 3.5, Elements {'Sb': 40.28, 'As': 1.3, 'Pb': 36.08, 'S': 22.33}
	Found duplicates of "Playfairite", with these properties :
			Density 5.8, Hardness 3.75, Elements {'Sb': 31.83, 'Pb': 48.15, 'S': 20.03}
			Density 5.8, Hardness 3.75, Elements {'Sb': 31.83, 'Pb': 48.15, 'S': 20.03}
	Found duplicates of "Sterryite", with these properties :
			Density 6.0, Hardness 3.5, Elements {'Ag': 4.75, 'Sb': 24.15, 'As': 4.95, 'Pb': 45.66, 'S': 20.49}
			Density 6.0, Hardness 3.5, Elements {'Ag': 4.75, 'Sb': 24.15, 'As': 4.95, 'Pb': 45.66, 'S': 20.49}
	Found duplicates of "Sorbyite", with these properties :
			Density 5.52, Hardness 3.75, Elements {'Sb': 3.61, 'As': 0.39, 'Pb': 68.62, 'S': 27.39}
			Density 5.52, Hardness 3.75, Elements {'Sb': 3.61, 'As': 0.39, 'Pb': 68.62, 'S': 27.39}
	Found duplicates of "Tintinaite", with these properties :
			Density 5.48, Hardness 2.75, Elements {'Cu': 2.2, 'Ag': 0.12, 'Bi': 1.56, 'Sb': 34.42, 'Pb': 40.7, 'S': 21.0}
			Density 5.48, Hardness 2.75, Elements {'Cu': 2.2, 'Ag': 0.12, 'Bi': 1.56, 'Sb': 34.42, 'Pb': 40.7, 'S': 21.0}
	Found duplicates of "Weloganite", with these properties :
			Density 3.22, Hardness 3.5, Elements {'Na': 5.65, 'Sr': 32.29, 'Zr': 11.2, 'H': 0.74, 'C': 8.85, 'O': 41.27}
			Density 3.22, Hardness 3.5, Elements {'Na': 5.65, 'Sr': 32.29, 'Zr': 11.2, 'H': 0.74, 'C': 8.85, 'O': 41.27}
	Found duplicates of "Dadsonite", with these properties :
			Density 5.72, Hardness 2.5, Elements {'Sb': 31.29, 'Pb': 48.61, 'S': 19.7, 'Cl': 0.4}
			Density 5.72, Hardness 2.5, Elements {'Sb': 31.29, 'Pb': 48.61, 'S': 19.7, 'Cl': 0.4}
	Found duplicates of "Dresserite", with these properties :
			Density 2.96, Hardness 2.75, Elements {'Ba': 34.56, 'Al': 13.58, 'H': 1.52, 'C': 6.05, 'O': 44.29}
			Density 2.96, Hardness 2.75, Elements {'Ba': 34.56, 'Al': 13.58, 'H': 1.52, 'C': 6.05, 'O': 44.29}
	Found duplicates of "Akdalaite", with these properties :
			Density 3.68, Hardness 7.0, Elements {'Al': 51.12, 'H': 0.38, 'O': 48.5}
			Density 3.68, Hardness 7.0, Elements {'Al': 51.12, 'H': 0.38, 'O': 48.5}
			Density 3.68, Hardness 7.0, Elements {'Al': 51.12, 'H': 0.38, 'O': 48.5}
	Found duplicates of "Romarchite", with these properties :
			Density None, Hardness 2.25, Elements {'Sn': 88.12, 'O': 11.88}
			Density None, Hardness 2.25, Elements {'Sn': 88.12, 'O': 11.88}
	Found duplicates of "Wakefieldite-Y", with these properties :
			Density 4.21, Hardness 5.0, Elements {'Y': 43.61, 'V': 24.99, 'O': 31.4}
			Density 4.21, Hardness 5.0, Elements {'Y': 43.61, 'V': 24.99, 'O': 31.4}
	Found duplicates of "Carletonite", with these properties :
			Density 2.45, Hardness 4.25, Elements {'K': 2.85, 'Na': 7.83, 'Ca': 14.63, 'Si': 21.86, 'H': 0.26, 'C': 4.27, 'O': 47.57, 'F': 0.74}
			Density 2.45, Hardness 4.25, Elements {'K': 2.85, 'Na': 7.83, 'Ca': 14.63, 'Si': 21.86, 'H': 0.26, 'C': 4.27, 'O': 47.57, 'F': 0.74}
	Found duplicates of "Athabascaite", with these properties :
			Density None, Hardness 2.75, Elements {'Cu': 50.15, 'Se': 49.85}
			Density None, Hardness 2.75, Elements {'Cu': 50.15, 'Se': 49.85}
	Found duplicates of "Clinosafflorite", with these properties :
			Density 7.46, Hardness 4.75, Elements {'Fe': 8.06, 'Co': 17.01, 'Ni': 2.82, 'As': 72.1}
			Density 7.46, Hardness 4.75, Elements {'Fe': 8.06, 'Co': 17.01, 'Ni': 2.82, 'As': 72.1}
	Found duplicates of "Prassoite", with these properties :
			Density 7.6, Hardness 5.5, Elements {'Rh': 78.43, 'S': 21.57}
			Density 7.6, Hardness 5.5, Elements {'Rh': 78.43, 'S': 21.57}
	Found duplicates of "Cuprospinel", with these properties :
			Density 5.09, Hardness 6.75, Elements {'Mg': 1.06, 'Al': 1.18, 'Fe': 47.58, 'Cu': 22.21, 'O': 27.96}
			Density 5.09, Hardness 6.75, Elements {'Mg': 1.06, 'Al': 1.18, 'Fe': 47.58, 'Cu': 22.21, 'O': 27.96}
	Found duplicates of "Tellurantimony", with these properties :
			Density None, Hardness 2.25, Elements {'Sb': 38.88, 'Te': 61.12}
			Density None, Hardness 2.25, Elements {'Sb': 38.88, 'Te': 61.12}
	Found duplicates of "Tulameenite", with these properties :
			Density 14.9, Hardness 5.0, Elements {'Fe': 10.96, 'Cu': 12.47, 'Pt': 76.57}
			Density 14.9, Hardness 5.0, Elements {'Fe': 10.96, 'Cu': 12.47, 'Pt': 76.57}
	Found duplicates of "Temagamite", with these properties :
			Density 9.5, Hardness 2.5, Elements {'Hg': 22.22, 'Te': 42.41, 'Pd': 35.37}
			Density 9.5, Hardness 2.5, Elements {'Hg': 22.22, 'Te': 42.41, 'Pd': 35.37}
	Found duplicates of "Agrellite", with these properties :
			Density 2.88, Hardness 5.5, Elements {'Na': 5.83, 'Ca': 20.32, 'Si': 28.48, 'O': 40.56, 'F': 4.82}
			Density 2.88, Hardness 5.5, Elements {'Na': 5.83, 'Ca': 20.32, 'Si': 28.48, 'O': 40.56, 'F': 4.82}
	Found duplicates of "Caysichite-Y", with these properties :
			Density 3.03, Hardness 4.5, Elements {'Ca': 8.81, 'Gd': 3.84, 'Y': 21.71, 'Si': 13.71, 'H': 0.86, 'C': 4.88, 'O': 46.19}
			Density 3.03, Hardness 4.5, Elements {'Ca': 8.81, 'Gd': 3.84, 'Y': 21.71, 'Si': 13.71, 'H': 0.86, 'C': 4.88, 'O': 46.19}
	Found duplicates of "Sudburyite", with these properties :
			Density None, Hardness 4.5, Elements {'Ni': 6.79, 'Sb': 56.3, 'Pd': 36.91}
			Density None, Hardness 4.5, Elements {'Ni': 6.79, 'Sb': 56.3, 'Pd': 36.91}
	Found duplicates of "Cowlesite", with these properties :
			Density 2.14, Hardness 5.25, Elements {'Ca': 8.98, 'Al': 12.09, 'Si': 18.88, 'H': 2.71, 'O': 57.35}
			Density 2.14, Hardness 5.25, Elements {'Ca': 8.98, 'Al': 12.09, 'Si': 18.88, 'H': 2.71, 'O': 57.35}
	Found duplicates of "Baricite", with these properties :
			Density 2.42, Hardness 1.75, Elements {'Mg': 12.7, 'Fe': 9.73, 'P': 14.39, 'H': 3.74, 'O': 59.44}
			Density 2.42, Hardness 1.75, Elements {'Mg': 12.7, 'Fe': 9.73, 'P': 14.39, 'H': 3.74, 'O': 59.44}
	Found duplicates of "Rucklidgeite", with these properties :
			Density 7.739, Hardness 2.5, Elements {'Bi': 41.39, 'Te': 44.93, 'Pb': 13.68}
			Density 7.739, Hardness 2.5, Elements {'Bi': 41.39, 'Te': 44.93, 'Pb': 13.68}
	Found duplicates of "Satterlyite", with these properties :
			Density 3.68, Hardness 4.75, Elements {'Mg': 5.85, 'Fe': 40.29, 'P': 14.9, 'H': 0.48, 'O': 38.48}
			Density 3.68, Hardness 4.75, Elements {'Mg': 5.85, 'Fe': 40.29, 'P': 14.9, 'H': 0.48, 'O': 38.48}
	Found duplicates of "Cernyite", with these properties :
			Density 4.776, Hardness 4.0, Elements {'Cd': 23.11, 'Cu': 26.12, 'Sn': 24.4, 'S': 26.37}
			Density 4.776, Hardness 4.0, Elements {'Cd': 23.11, 'Cu': 26.12, 'Sn': 24.4, 'S': 26.37}
	Found duplicates of "Strontiodresserite", with these properties :
			Density 2.71, Hardness 2.5, Elements {'Sr': 19.57, 'Ca': 2.98, 'Al': 16.07, 'H': 1.8, 'C': 7.15, 'O': 52.42}
			Density 2.71, Hardness 2.5, Elements {'Sr': 19.57, 'Ca': 2.98, 'Al': 16.07, 'H': 1.8, 'C': 7.15, 'O': 52.42}
	Found duplicates of "Boyleite", with these properties :
			Density 2.41, Hardness 2.0, Elements {'Mg': 2.72, 'Zn': 21.97, 'H': 3.61, 'S': 14.36, 'O': 57.33}
			Density 2.41, Hardness 2.0, Elements {'Mg': 2.72, 'Zn': 21.97, 'H': 3.61, 'S': 14.36, 'O': 57.33}
	Found duplicates of "Donnayite-Y", with these properties :
			Density 3.3, Hardness 3.0, Elements {'Na': 2.77, 'Sr': 31.71, 'Ca': 4.83, 'Y': 10.73, 'H': 0.73, 'C': 8.69, 'O': 40.53}
			Density 3.3, Hardness 3.0, Elements {'Na': 2.77, 'Sr': 31.71, 'Ca': 4.83, 'Y': 10.73, 'H': 0.73, 'C': 8.69, 'O': 40.53}
	Found duplicates of "Yarrowite", with these properties :
			Density 4.89, Hardness 3.5, Elements {'Cu': 69.03, 'S': 30.97}
			Density 4.89, Hardness 3.5, Elements {'Cu': 69.03, 'S': 30.97}
	Found duplicates of "Spionkopite", with these properties :
			Density 5.13, Hardness 2.75, Elements {'Cu': 73.51, 'S': 26.49}
			Density 5.13, Hardness 2.75, Elements {'Cu': 73.51, 'S': 26.49}
	Found duplicates of "Prosperite", with these properties :
			Density 4.31, Hardness 4.5, Elements {'Ca': 8.59, 'Zn': 28.02, 'As': 32.11, 'H': 0.43, 'O': 30.85}
			Density 4.31, Hardness 4.5, Elements {'Ca': 8.59, 'Zn': 28.02, 'As': 32.11, 'H': 0.43, 'O': 30.85}
	Found duplicates of "Sabinaite", with these properties :
			Density 3.36, Hardness None, Elements {'Na': 12.67, 'Ca': 3.16, 'Zr': 28.74, 'Ti': 7.54, 'C': 7.57, 'O': 40.32}
			Density 3.36, Hardness None, Elements {'Na': 12.67, 'Ca': 3.16, 'Zr': 28.74, 'Ti': 7.54, 'C': 7.57, 'O': 40.32}
	Found duplicates of "Povondraite", with these properties :
			Density 3.26, Hardness 7.0, Elements {'K': 0.85, 'Na': 1.49, 'Mg': 3.78, 'Al': 1.4, 'Fe': 31.87, 'Si': 14.57, 'B': 2.8, 'H': 0.35, 'O': 42.89}
			Density 3.26, Hardness 7.0, Elements {'K': 0.85, 'Na': 1.49, 'Mg': 3.78, 'Al': 1.4, 'Fe': 31.87, 'Si': 14.57, 'B': 2.8, 'H': 0.35, 'O': 42.89}
			Density 3.26, Hardness 7.0, Elements {'K': 0.85, 'Na': 1.49, 'Mg': 3.78, 'Al': 1.4, 'Fe': 31.87, 'Si': 14.57, 'B': 2.8, 'H': 0.35, 'O': 42.89}
	Found duplicates of "Wicksite", with these properties :
			Density 3.54, Hardness 4.75, Elements {'Na': 2.27, 'Ca': 7.92, 'Mg': 2.4, 'Mn': 5.43, 'Fe': 22.08, 'P': 18.37, 'H': 0.4, 'O': 41.12}
			Density 3.54, Hardness 4.75, Elements {'Na': 2.27, 'Ca': 7.92, 'Mg': 2.4, 'Mn': 5.43, 'Fe': 22.08, 'P': 18.37, 'H': 0.4, 'O': 41.12}
	Found duplicates of "Tancoite", with these properties :
			Density 2.752, Hardness 4.25, Elements {'Na': 15.97, 'Li': 2.41, 'Al': 9.37, 'P': 21.52, 'H': 0.7, 'O': 50.02}
			Density 2.752, Hardness 4.25, Elements {'Na': 15.97, 'Li': 2.41, 'Al': 9.37, 'P': 21.52, 'H': 0.7, 'O': 50.02}
	Found duplicates of "Petarasite", with these properties :
			Density 2.88, Hardness 5.25, Elements {'Na': 12.97, 'Ca': 0.48, 'Zr': 21.9, 'Si': 20.23, 'H': 0.8, 'Cl': 2.13, 'O': 41.49}
			Density 2.88, Hardness 5.25, Elements {'Na': 12.97, 'Ca': 0.48, 'Zr': 21.9, 'Si': 20.23, 'H': 0.8, 'Cl': 2.13, 'O': 41.49}
	Found duplicates of "Stibivanite", with these properties :
			Density 5.12, Hardness 4.0, Elements {'V': 13.6, 'Sb': 65.03, 'O': 21.36}
			Density 5.12, Hardness 4.0, Elements {'V': 13.6, 'Sb': 65.03, 'O': 21.36}
	Found duplicates of "Spertiniite", with these properties :
			Density 3.93, Hardness None, Elements {'Cu': 65.13, 'H': 2.07, 'O': 32.8}
			Density 3.93, Hardness None, Elements {'Cu': 65.13, 'H': 2.07, 'O': 32.8}
	Found duplicates of "Doyleite", with these properties :
			Density 2.48, Hardness 2.75, Elements {'Al': 34.59, 'H': 3.88, 'O': 61.53}
			Density 2.48, Hardness 2.75, Elements {'Al': 34.59, 'H': 3.88, 'O': 61.53}
	Found duplicates of "Sturmanite", with these properties :
			Density 1.847, Hardness 2.5, Elements {'Ca': 19.0, 'Mn': 1.3, 'Al': 1.28, 'Fe': 5.3, 'B': 0.85, 'H': 5.26, 'S': 5.07, 'O': 61.95}
			Density 1.847, Hardness 2.5, Elements {'Ca': 19.0, 'Mn': 1.3, 'Al': 1.28, 'Fe': 5.3, 'B': 0.85, 'H': 5.26, 'S': 5.07, 'O': 61.95}
	Found duplicates of "Wadsleyite", with these properties :
			Density 3.84, Hardness None, Elements {'Mg': 23.3, 'Fe': 17.85, 'Si': 17.95, 'O': 40.9}
			Density 3.84, Hardness None, Elements {'Mg': 23.3, 'Fe': 17.85, 'Si': 17.95, 'O': 40.9}
	Found duplicates of "Potassic-magnesiosadanagaite", with these properties :
			Density 3.27, Hardness 6.0, Elements {'K': 3.26, 'Na': 0.64, 'Ca': 8.9, 'Mg': 6.75, 'Ti': 1.33, 'Al': 8.24, 'Fe': 9.3, 'Si': 18.71, 'H': 0.22, 'O': 42.64}
			Density 3.27, Hardness 6.0, Elements {'K': 3.26, 'Na': 0.64, 'Ca': 8.9, 'Mg': 6.75, 'Ti': 1.33, 'Al': 8.24, 'Fe': 9.3, 'Si': 18.71, 'H': 0.22, 'O': 42.64}
	Found duplicates of "Simonkolleite", with these properties :
			Density 3.2, Hardness 1.5, Elements {'Zn': 59.24, 'H': 1.83, 'Cl': 12.85, 'O': 26.09}
			Density 3.2, Hardness 1.5, Elements {'Zn': 59.24, 'H': 1.83, 'Cl': 12.85, 'O': 26.09}
	Found duplicates of "Chenite", with these properties :
			Density 5.98, Hardness 2.5, Elements {'Cu': 5.36, 'H': 0.51, 'Pb': 69.85, 'S': 5.41, 'O': 18.88}
			Density 5.98, Hardness 2.5, Elements {'Cu': 5.36, 'H': 0.51, 'Pb': 69.85, 'S': 5.41, 'O': 18.88}
	Found duplicates of "Ferrowodginite", with these properties :
			Density None, Hardness 5.5, Elements {'Ta': 54.47, 'Fe': 8.41, 'Sn': 17.87, 'O': 19.26}
			Density None, Hardness 5.5, Elements {'Ta': 54.47, 'Fe': 8.41, 'Sn': 17.87, 'O': 19.26}
	Found duplicates of "Titanowodginite", with these properties :
			Density 6.86, Hardness 5.5, Elements {'Ta': 49.62, 'Ti': 4.92, 'Mn': 7.53, 'Nb': 7.96, 'Fe': 1.91, 'Sn': 6.1, 'O': 21.94}
			Density 6.86, Hardness 5.5, Elements {'Ta': 49.62, 'Ti': 4.92, 'Mn': 7.53, 'Nb': 7.96, 'Fe': 1.91, 'Sn': 6.1, 'O': 21.94}
	Found duplicates of "Rapidcreekite", with these properties :
			Density 2.21, Hardness 2.0, Elements {'Ca': 26.0, 'H': 2.62, 'C': 3.9, 'S': 10.4, 'O': 57.09}
			Density 2.21, Hardness 2.0, Elements {'Ca': 26.0, 'H': 2.62, 'C': 3.9, 'S': 10.4, 'O': 57.09}
	Found duplicates of "Bobfergusonite", with these properties :
			Density 3.54, Hardness 4.0, Elements {'Na': 4.72, 'Mn': 28.22, 'Al': 2.77, 'Fe': 5.74, 'P': 19.09, 'O': 39.45}
			Density 3.54, Hardness 4.0, Elements {'Na': 4.72, 'Mn': 28.22, 'Al': 2.77, 'Fe': 5.74, 'P': 19.09, 'O': 39.45}
	Found duplicates of "Watkinsonite", with these properties :
			Density None, Hardness 3.5, Elements {'Cu': 7.34, 'Bi': 48.28, 'Pb': 11.97, 'Se': 29.64, 'S': 2.78}
			Density None, Hardness 3.5, Elements {'Cu': 7.34, 'Bi': 48.28, 'Pb': 11.97, 'Se': 29.64, 'S': 2.78}
	Found duplicates of "Thornasite", with these properties :
			Density 2.62, Hardness None, Elements {'Na': 8.09, 'Th': 20.41, 'Si': 26.35, 'H': 1.06, 'O': 44.09}
			Density 2.62, Hardness None, Elements {'Na': 8.09, 'Th': 20.41, 'Si': 26.35, 'H': 1.06, 'O': 44.09}
	Found duplicates of "Petrukite", with these properties :
			Density 4.61, Hardness 4.5, Elements {'Zn': 3.44, 'In': 6.05, 'Fe': 8.82, 'Cu': 20.08, 'Ag': 2.84, 'Sn': 25.0, 'S': 33.77}
			Density 4.61, Hardness 4.5, Elements {'Zn': 3.44, 'In': 6.05, 'Fe': 8.82, 'Cu': 20.08, 'Ag': 2.84, 'Sn': 25.0, 'S': 33.77}
	Found duplicates of "Cabriite", with these properties :
			Density 10.7, Hardness 4.25, Elements {'Cu': 16.08, 'Sn': 30.05, 'Pd': 53.87}
			Density 10.7, Hardness 4.25, Elements {'Cu': 16.08, 'Sn': 30.05, 'Pd': 53.87}
	Found duplicates of "Protoferro-anthophyllite", with these properties :
			Density 3.61, Hardness None, Elements {'Mg': 2.51, 'Mn': 5.67, 'Fe': 28.81, 'Si': 23.18, 'H': 0.21, 'O': 39.62}
			Density 3.61, Hardness None, Elements {'Mg': 2.51, 'Mn': 5.67, 'Fe': 28.81, 'Si': 23.18, 'H': 0.21, 'O': 39.62}
	Found duplicates of "Protomangano-ferro-anthophyllite", with these properties :
			Density 3.5, Hardness 5.5, Elements {'Mg': 2.51, 'Mn': 5.67, 'Fe': 28.81, 'Si': 23.18, 'H': 0.21, 'O': 39.62}
			Density 3.5, Hardness 5.5, Elements {'Mg': 2.51, 'Mn': 5.67, 'Fe': 28.81, 'Si': 23.18, 'H': 0.21, 'O': 39.62}
	Found duplicates of "Ferrilotharmeyerite", with these properties :
			Density 4.25, Hardness 3.0, Elements {'Ca': 7.35, 'Zn': 10.66, 'Fe': 10.24, 'Cu': 3.88, 'As': 30.53, 'H': 0.51, 'Pb': 4.22, 'O': 32.6}
			Density 4.25, Hardness 3.0, Elements {'Ca': 7.35, 'Zn': 10.66, 'Fe': 10.24, 'Cu': 3.88, 'As': 30.53, 'H': 0.51, 'Pb': 4.22, 'O': 32.6}
	Found duplicates of "Poudretteite", with these properties :
			Density 2.51, Hardness 5.0, Elements {'K': 4.28, 'Na': 5.04, 'Si': 36.92, 'B': 1.18, 'O': 52.58}
			Density 2.51, Hardness 5.0, Elements {'K': 4.28, 'Na': 5.04, 'Si': 36.92, 'B': 1.18, 'O': 52.58}
	Found duplicates of "Skippenite", with these properties :
			Density None, Hardness 2.5, Elements {'Bi': 61.5, 'Te': 14.08, 'Se': 23.24, 'S': 1.18}
			Density None, Hardness 2.5, Elements {'Bi': 61.5, 'Te': 14.08, 'Se': 23.24, 'S': 1.18}
	Found duplicates of "Potassic-fluororichterite", with these properties :
			Density None, Hardness None, Elements {'K': 3.51, 'Na': 3.44, 'Ca': 4.8, 'Mg': 14.57, 'Si': 26.93, 'O': 42.19, 'F': 4.55}
			Density None, Hardness None, Elements {'K': 3.51, 'Na': 3.44, 'Ca': 4.8, 'Mg': 14.57, 'Si': 26.93, 'O': 42.19, 'F': 4.55}
			Density None, Hardness None, Elements {'K': 3.51, 'Na': 3.44, 'Ca': 4.8, 'Mg': 14.57, 'Si': 26.93, 'O': 42.19, 'F': 4.55}
	Found duplicates of "Bearthite", with these properties :
			Density None, Hardness None, Elements {'Ca': 25.52, 'Al': 8.59, 'P': 19.72, 'H': 0.32, 'O': 45.85}
			Density None, Hardness None, Elements {'Ca': 25.52, 'Al': 8.59, 'P': 19.72, 'H': 0.32, 'O': 45.85}
	Found duplicates of "Zanazziite", with these properties :
			Density 2.76, Hardness 5.0, Elements {'Ca': 7.68, 'Mg': 6.98, 'Mn': 1.05, 'Be': 3.45, 'Al': 0.78, 'Fe': 8.56, 'P': 17.8, 'H': 1.6, 'O': 52.1}
			Density 2.76, Hardness 5.0, Elements {'Ca': 7.68, 'Mg': 6.98, 'Mn': 1.05, 'Be': 3.45, 'Al': 0.78, 'Fe': 8.56, 'P': 17.8, 'H': 1.6, 'O': 52.1}
	Found duplicates of "Edoylerite", with these properties :
			Density None, Hardness None, Elements {'Cr': 6.65, 'Hg': 76.96, 'S': 8.2, 'O': 8.18}
			Density None, Hardness None, Elements {'Cr': 6.65, 'Hg': 76.96, 'S': 8.2, 'O': 8.18}
	Found duplicates of "Donharrisite", with these properties :
			Density None, Hardness 2.0, Elements {'Ni': 34.53, 'Hg': 44.25, 'S': 21.22}
			Density None, Hardness 2.0, Elements {'Ni': 34.53, 'Hg': 44.25, 'S': 21.22}
	Found duplicates of "Squawcreekite", with these properties :
			Density None, Hardness 6.25, Elements {'Fe': 19.18, 'Sb': 41.81, 'H': 0.77, 'W': 7.72, 'O': 30.52}
			Density None, Hardness 6.25, Elements {'Fe': 19.18, 'Sb': 41.81, 'H': 0.77, 'W': 7.72, 'O': 30.52}
	Found duplicates of "Stalderite", with these properties :
			Density 4.97, Hardness 3.75, Elements {'Tl': 26.85, 'Zn': 9.45, 'Fe': 5.13, 'Cu': 8.35, 'Hg': 5.27, 'As': 19.68, 'S': 25.27}
			Density 4.97, Hardness 3.75, Elements {'Tl': 26.85, 'Zn': 9.45, 'Fe': 5.13, 'Cu': 8.35, 'Hg': 5.27, 'As': 19.68, 'S': 25.27}
	Found duplicates of "Erniggliite", with these properties :
			Density None, Hardness 2.5, Elements {'Tl': 47.0, 'Sn': 13.65, 'As': 17.23, 'S': 22.12}
			Density None, Hardness 2.5, Elements {'Tl': 47.0, 'Sn': 13.65, 'As': 17.23, 'S': 22.12}
	Found duplicates of "Edenharterite", with these properties :
			Density None, Hardness 2.75, Elements {'Tl': 22.89, 'As': 25.17, 'Pb': 23.21, 'S': 28.73}
			Density None, Hardness 2.75, Elements {'Tl': 22.89, 'As': 25.17, 'Pb': 23.21, 'S': 28.73}
	Found duplicates of "Wattersite", with these properties :
			Density 8.91, Hardness 4.5, Elements {'Cr': 9.47, 'Hg': 73.05, 'O': 17.48}
			Density 8.91, Hardness 4.5, Elements {'Cr': 9.47, 'Hg': 73.05, 'O': 17.48}
	Found duplicates of "Criddleite", with these properties :
			Density None, Hardness 3.25, Elements {'Tl': 8.02, 'Ag': 8.46, 'Sb': 47.76, 'Au': 23.18, 'S': 12.58}
			Density None, Hardness 3.25, Elements {'Tl': 8.02, 'Ag': 8.46, 'Sb': 47.76, 'Au': 23.18, 'S': 12.58}
	Found duplicates of "Wadalite", with these properties :
			Density 3.06, Hardness None, Elements {'Ca': 30.23, 'Mg': 1.83, 'Al': 11.19, 'Fe': 2.11, 'Si': 9.53, 'Cl': 12.92, 'O': 32.18}
			Density 3.06, Hardness None, Elements {'Ca': 30.23, 'Mg': 1.83, 'Al': 11.19, 'Fe': 2.11, 'Si': 9.53, 'Cl': 12.92, 'O': 32.18}
	Found duplicates of "Ferroalluaudite", with these properties :
			Density None, Hardness 5.0, Elements {'Na': 5.32, 'Ca': 0.84, 'Mg': 1.02, 'Mn': 5.78, 'Fe': 27.04, 'P': 19.56, 'O': 40.42}
			Density None, Hardness 5.0, Elements {'Na': 5.32, 'Ca': 0.84, 'Mg': 1.02, 'Mn': 5.78, 'Fe': 27.04, 'P': 19.56, 'O': 40.42}
			Density None, Hardness 5.0, Elements {'Na': 5.32, 'Ca': 0.84, 'Mg': 1.02, 'Mn': 5.78, 'Fe': 27.04, 'P': 19.56, 'O': 40.42}
	Found duplicates of "Arupite", with these properties :
			Density None, Hardness 1.75, Elements {'Mg': 1.85, 'Mn': 12.56, 'Fe': 4.26, 'Ni': 13.42, 'P': 12.59, 'H': 3.28, 'O': 52.04}
			Density None, Hardness 1.75, Elements {'Mg': 1.85, 'Mn': 12.56, 'Fe': 4.26, 'Ni': 13.42, 'P': 12.59, 'H': 3.28, 'O': 52.04}
	Found duplicates of "Buckhornite", with these properties :
			Density None, Hardness 2.5, Elements {'Bi': 17.83, 'Te': 21.78, 'Pb': 35.37, 'Au': 16.81, 'S': 8.21}
			Density None, Hardness 2.5, Elements {'Bi': 17.83, 'Te': 21.78, 'Pb': 35.37, 'Au': 16.81, 'S': 8.21}
	Found duplicates of "Werdingite", with these properties :
			Density 3.04, Hardness 7.0, Elements {'Mg': 2.81, 'Al': 31.69, 'Fe': 4.16, 'Si': 9.29, 'B': 3.22, 'O': 48.83}
			Density 3.04, Hardness 7.0, Elements {'Mg': 2.81, 'Al': 31.69, 'Fe': 4.16, 'Si': 9.29, 'B': 3.22, 'O': 48.83}
	Found duplicates of "Edgarbaileyite", with these properties :
			Density 9.4, Hardness 4.0, Elements {'Si': 4.09, 'Hg': 87.74, 'O': 8.16}
			Density 9.4, Hardness 4.0, Elements {'Si': 4.09, 'Hg': 87.74, 'O': 8.16}
	Found duplicates of "Voggite", with these properties :
			Density 2.7, Hardness None, Elements {'Na': 13.32, 'Zr': 26.42, 'P': 8.97, 'H': 1.46, 'C': 3.48, 'O': 46.35}
			Density 2.7, Hardness None, Elements {'Na': 13.32, 'Zr': 26.42, 'P': 8.97, 'H': 1.46, 'C': 3.48, 'O': 46.35}
	Found duplicates of "Tuliokite", with these properties :
			Density 3.15, Hardness 3.5, Elements {'Ba': 14.08, 'Na': 14.14, 'Th': 23.79, 'H': 1.24, 'C': 7.39, 'O': 39.36}
			Density 3.15, Hardness 3.5, Elements {'Ba': 14.08, 'Na': 14.14, 'Th': 23.79, 'H': 1.24, 'C': 7.39, 'O': 39.36}
	Found duplicates of "Wawayandaite", with these properties :
			Density 3.0, Hardness 1.0, Elements {'Ca': 19.06, 'Mn': 8.71, 'Be': 6.43, 'Si': 13.36, 'B': 0.86, 'H': 1.08, 'Cl': 4.22, 'O': 46.29}
			Density 3.0, Hardness 1.0, Elements {'Ca': 19.06, 'Mn': 8.71, 'Be': 6.43, 'Si': 13.36, 'B': 0.86, 'H': 1.08, 'Cl': 4.22, 'O': 46.29}
	Found duplicates of "Vihorlatite", with these properties :
			Density 8.0, Hardness None, Elements {'Bi': 70.14, 'Te': 8.02, 'Se': 21.06, 'S': 0.79}
			Density 8.0, Hardness None, Elements {'Bi': 70.14, 'Te': 8.02, 'Se': 21.06, 'S': 0.79}
	Found duplicates of "Alluaivite", with these properties :
			Density 2.76, Hardness 5.5, Elements {'Na': 20.14, 'Ca': 8.31, 'Ti': 4.97, 'Mn': 3.8, 'Nb': 3.21, 'Si': 20.72, 'H': 0.19, 'Cl': 3.27, 'O': 35.4}
			Density 2.76, Hardness 5.5, Elements {'Na': 20.14, 'Ca': 8.31, 'Ti': 4.97, 'Mn': 3.8, 'Nb': 3.21, 'Si': 20.72, 'H': 0.19, 'Cl': 3.27, 'O': 35.4}
	Found duplicates of "Wilkinsonite", with these properties :
			Density None, Hardness 5.0, Elements {'Na': 5.29, 'Fe': 38.53, 'Si': 19.38, 'O': 36.8}
			Density None, Hardness 5.0, Elements {'Na': 5.29, 'Fe': 38.53, 'Si': 19.38, 'O': 36.8}
	Found duplicates of "Yingjiangite", with these properties :
			Density 4.54, Hardness 3.5, Elements {'K': 4.5, 'Ca': 0.38, 'U': 63.9, 'P': 4.75, 'H': 0.7, 'O': 25.77}
			Density 4.54, Hardness 3.5, Elements {'K': 4.5, 'Ca': 0.38, 'U': 63.9, 'P': 4.75, 'H': 0.7, 'O': 25.77}
	Found duplicates of "Vyalsovite", with these properties :
			Density 1.96, Hardness None, Elements {'Ca': 16.7, 'Al': 11.24, 'Fe': 23.27, 'H': 2.1, 'S': 13.36, 'O': 33.33}
			Density 1.96, Hardness None, Elements {'Ca': 16.7, 'Al': 11.24, 'Fe': 23.27, 'H': 2.1, 'S': 13.36, 'O': 33.33}
	Found duplicates of "Roshchinite", with these properties :
			Density 5.27, Hardness 3.0, Elements {'Cu': 0.99, 'Ag': 15.9, 'Sb': 43.46, 'As': 2.91, 'Pb': 12.86, 'S': 23.89}
			Density 5.27, Hardness 3.0, Elements {'Cu': 0.99, 'Ag': 15.9, 'Sb': 43.46, 'As': 2.91, 'Pb': 12.86, 'S': 23.89}
	Found duplicates of "Toyohaite", with these properties :
			Density 4.94, Hardness 4.0, Elements {'Fe': 6.32, 'Ag': 24.4, 'Sn': 40.28, 'S': 29.01}
			Density 4.94, Hardness 4.0, Elements {'Fe': 6.32, 'Ag': 24.4, 'Sn': 40.28, 'S': 29.01}
	Found duplicates of "Calcioancylite-Nd", with these properties :
			Density None, Hardness 4.25, Elements {'Ca': 11.81, 'H': 0.89, 'C': 7.08, 'Nd': 42.5, 'O': 37.72}
			Density None, Hardness 4.25, Elements {'Ca': 11.81, 'H': 0.89, 'C': 7.08, 'Nd': 42.5, 'O': 37.72}
			Density None, Hardness 4.25, Elements {'Ca': 11.81, 'H': 0.89, 'C': 7.08, 'Nd': 42.5, 'O': 37.72}
	Found duplicates of "Boggsite", with these properties :
			Density 1.98, Hardness 3.5, Elements {'Na': 1.25, 'Ca': 4.34, 'Al': 7.31, 'Si': 28.91, 'H': 1.86, 'O': 56.34}
			Density 1.98, Hardness 3.5, Elements {'Na': 1.25, 'Ca': 4.34, 'Al': 7.31, 'Si': 28.91, 'H': 1.86, 'O': 56.34}
	Found duplicates of "Dmisteinbergite", with these properties :
			Density None, Hardness 6.0, Elements {'Ca': 14.41, 'Al': 19.4, 'Si': 20.19, 'O': 46.01}
			Density None, Hardness 6.0, Elements {'Ca': 14.41, 'Al': 19.4, 'Si': 20.19, 'O': 46.01}
	Found duplicates of "Damaraite", with these properties :
			Density None, Hardness 3.0, Elements {'H': 0.14, 'Pb': 88.04, 'Cl': 5.02, 'O': 6.8}
			Density None, Hardness 3.0, Elements {'H': 0.14, 'Pb': 88.04, 'Cl': 5.02, 'O': 6.8}
	Found duplicates of "Rorisite", with these properties :
			Density 2.78, Hardness 2.0, Elements {'Ca': 33.18, 'Mg': 6.71, 'Cl': 39.14, 'F': 20.97}
			Density 2.78, Hardness 2.0, Elements {'Ca': 33.18, 'Mg': 6.71, 'Cl': 39.14, 'F': 20.97}
	Found duplicates of "Simferite", with these properties :
			Density 3.24, Hardness 5.0, Elements {'Li': 2.51, 'Mg': 8.78, 'Mn': 7.94, 'Fe': 12.11, 'P': 22.39, 'O': 46.26}
			Density 3.24, Hardness 5.0, Elements {'Li': 2.51, 'Mg': 8.78, 'Mn': 7.94, 'Fe': 12.11, 'P': 22.39, 'O': 46.26}
	Found duplicates of "Cheremnykhite", with these properties :
			Density None, Hardness 5.5, Elements {'V': 8.01, 'Zn': 15.43, 'Te': 10.04, 'Pb': 48.9, 'O': 17.62}
			Density None, Hardness 5.5, Elements {'V': 8.01, 'Zn': 15.43, 'Te': 10.04, 'Pb': 48.9, 'O': 17.62}
	Found duplicates of "Belendorffite", with these properties :
			Density 13.2, Hardness 3.25, Elements {'Cu': 26.99, 'Hg': 73.01}
			Density 13.2, Hardness 3.25, Elements {'Cu': 26.99, 'Hg': 73.01}
	Found duplicates of "Boromuscovite", with these properties :
			Density 2.81, Hardness 2.75, Elements {'K': 10.2, 'Al': 14.08, 'Si': 21.99, 'B': 2.82, 'H': 0.39, 'O': 48.02, 'F': 2.48}
			Density 2.81, Hardness 2.75, Elements {'K': 10.2, 'Al': 14.08, 'Si': 21.99, 'B': 2.82, 'H': 0.39, 'O': 48.02, 'F': 2.48}
	Found duplicates of "Radtkeite", with these properties :
			Density 7.0, Hardness 2.5, Elements {'Hg': 72.65, 'S': 7.74, 'I': 15.32, 'Cl': 4.28}
			Density 7.0, Hardness 2.5, Elements {'Hg': 72.65, 'S': 7.74, 'I': 15.32, 'Cl': 4.28}
	Found duplicates of "Piemontite-Sr", with these properties :
			Density 3.69, Hardness 6.0, Elements {'Sr': 11.7, 'Ca': 8.41, 'Mn': 11.52, 'Al': 9.26, 'Fe': 3.19, 'Si': 16.07, 'H': 0.19, 'O': 39.66}
			Density 3.69, Hardness 6.0, Elements {'Sr': 11.7, 'Ca': 8.41, 'Mn': 11.52, 'Al': 9.26, 'Fe': 3.19, 'Si': 16.07, 'H': 0.19, 'O': 39.66}
			Density 3.69, Hardness 6.0, Elements {'Sr': 11.7, 'Ca': 8.41, 'Mn': 11.52, 'Al': 9.26, 'Fe': 3.19, 'Si': 16.07, 'H': 0.19, 'O': 39.66}
	Found duplicates of "Astrocyanite-Ce", with these properties :
			Density 3.8, Hardness 2.5, Elements {'La': 2.92, 'Ce': 1.47, 'Pr': 2.96, 'Sm': 1.58, 'U': 25.02, 'Cu': 13.36, 'H': 0.78, 'C': 6.31, 'Nd': 9.1, 'O': 36.49}
			Density 3.8, Hardness 2.5, Elements {'La': 2.92, 'Ce': 1.47, 'Pr': 2.96, 'Sm': 1.58, 'U': 25.02, 'Cu': 13.36, 'H': 0.78, 'C': 6.31, 'Nd': 9.1, 'O': 36.49}
	Found duplicates of "Znucalite", with these properties :
			Density 3.05, Hardness None, Elements {'Ca': 2.47, 'U': 14.68, 'Zn': 44.36, 'H': 1.74, 'C': 2.22, 'O': 34.53}
			Density 3.05, Hardness None, Elements {'Ca': 2.47, 'U': 14.68, 'Zn': 44.36, 'H': 1.74, 'C': 2.22, 'O': 34.53}
	Found duplicates of "Wakefieldite-La", with these properties :
			Density None, Hardness 4.0, Elements {'La': 38.89, 'Pr': 6.11, 'Sm': 0.59, 'Y': 0.35, 'V': 20.29, 'Nd': 8.53, 'O': 25.24}
			Density None, Hardness 4.0, Elements {'La': 38.89, 'Pr': 6.11, 'Sm': 0.59, 'Y': 0.35, 'V': 20.29, 'Nd': 8.53, 'O': 25.24}
	Found duplicates of "Tschernichite", with these properties :
			Density 2.02, Hardness 4.5, Elements {'Na': 0.87, 'Ca': 4.57, 'Al': 8.2, 'Si': 25.59, 'H': 2.45, 'O': 58.32}
			Density 2.02, Hardness 4.5, Elements {'Na': 0.87, 'Ca': 4.57, 'Al': 8.2, 'Si': 25.59, 'H': 2.45, 'O': 58.32}
	Found duplicates of "Strontiowhitlockite", with these properties :
			Density 3.64, Hardness 5.0, Elements {'Ba': 1.97, 'Sr': 44.06, 'Ca': 4.03, 'Mg': 2.79, 'P': 15.35, 'H': 0.07, 'O': 31.72}
			Density 3.64, Hardness 5.0, Elements {'Ba': 1.97, 'Sr': 44.06, 'Ca': 4.03, 'Mg': 2.79, 'P': 15.35, 'H': 0.07, 'O': 31.72}
	Found duplicates of "Trimounsite-Y", with these properties :
			Density 5.0, Hardness 7.0, Elements {'RE': 15.15, 'Y': 28.06, 'Ti': 21.16, 'Si': 5.32, 'O': 30.3}
			Density 5.0, Hardness 7.0, Elements {'RE': 15.15, 'Y': 28.06, 'Ti': 21.16, 'Si': 5.32, 'O': 30.3}
	Found duplicates of "Yoshiokaite", with these properties :
			Density 2.83, Hardness None, Elements {'Ca': 24.14, 'Al': 32.5, 'Si': 2.26, 'O': 41.11}
			Density 2.83, Hardness None, Elements {'Ca': 24.14, 'Al': 32.5, 'Si': 2.26, 'O': 41.11}
	Found duplicates of "Szymanskiite", with these properties :
			Density None, Hardness None, Elements {'Mg': 0.82, 'Ni': 5.95, 'Hg': 72.34, 'H': 0.68, 'C': 3.25, 'O': 16.95}
			Density None, Hardness None, Elements {'Mg': 0.82, 'Ni': 5.95, 'Hg': 72.34, 'H': 0.68, 'C': 3.25, 'O': 16.95}
	Found duplicates of "Rouvilleite", with these properties :
			Density 2.67, Hardness 3.0, Elements {'Na': 19.81, 'Ca': 23.02, 'C': 10.35, 'O': 41.36, 'F': 5.46}
			Density 2.67, Hardness 3.0, Elements {'Na': 19.81, 'Ca': 23.02, 'C': 10.35, 'O': 41.36, 'F': 5.46}
	Found duplicates of "Sitinakite", with these properties :
			Density 2.86, Hardness 4.5, Elements {'K': 5.94, 'Na': 6.99, 'Ti': 21.83, 'Nb': 14.12, 'Si': 8.54, 'H': 1.26, 'O': 41.33}
			Density 2.86, Hardness 4.5, Elements {'K': 5.94, 'Na': 6.99, 'Ti': 21.83, 'Nb': 14.12, 'Si': 8.54, 'H': 1.26, 'O': 41.33}
	Found duplicates of "Belkovite", with these properties :
			Density 4.16, Hardness 6.5, Elements {'Ba': 29.27, 'Ti': 6.8, 'Nb': 26.4, 'Si': 7.98, 'O': 29.55}
			Density 4.16, Hardness 6.5, Elements {'Ba': 29.27, 'Ti': 6.8, 'Nb': 26.4, 'Si': 7.98, 'O': 29.55}
	Found duplicates of "Arsenogorceixite", with these properties :
			Density 3.65, Hardness 4.0, Elements {'Ba': 23.31, 'Al': 13.74, 'As': 22.25, 'P': 1.31, 'H': 1.11, 'O': 36.66, 'F': 1.61}
			Density 3.65, Hardness 4.0, Elements {'Ba': 23.31, 'Al': 13.74, 'As': 22.25, 'P': 1.31, 'H': 1.11, 'O': 36.66, 'F': 1.61}
	Found duplicates of "Barstowite", with these properties :
			Density 5.59, Hardness 3.0, Elements {'H': 0.18, 'Pb': 74.03, 'C': 1.07, 'Cl': 19.0, 'O': 5.72}
			Density 5.59, Hardness 3.0, Elements {'H': 0.18, 'Pb': 74.03, 'C': 1.07, 'Cl': 19.0, 'O': 5.72}
	Found duplicates of "Coombsite", with these properties :
			Density 3.0, Hardness None, Elements {'K': 1.84, 'Mg': 1.49, 'Mn': 20.14, 'Al': 5.71, 'Fe': 10.24, 'Si': 17.82, 'H': 0.66, 'O': 42.11}
			Density 3.0, Hardness None, Elements {'K': 1.84, 'Mg': 1.49, 'Mn': 20.14, 'Al': 5.71, 'Fe': 10.24, 'Si': 17.82, 'H': 0.66, 'O': 42.11}
	Found duplicates of "Dissakisite-Ce", with these properties :
			Density 3.75, Hardness 6.75, Elements {'Ca': 6.69, 'Ce': 10.52, 'RE': 15.62, 'Mg': 4.46, 'Al': 5.4, 'Fe': 8.39, 'Si': 14.06, 'H': 0.17, 'O': 34.7}
			Density 3.75, Hardness 6.75, Elements {'Ca': 6.69, 'Ce': 10.52, 'RE': 15.62, 'Mg': 4.46, 'Al': 5.4, 'Fe': 8.39, 'Si': 14.06, 'H': 0.17, 'O': 34.7}
	Found duplicates of "Clinotobermorite", with these properties :
			Density 2.58, Hardness 4.5, Elements {'Ca': 26.68, 'Si': 22.44, 'H': 1.88, 'O': 49.0}
			Density 2.58, Hardness 4.5, Elements {'Ca': 26.68, 'Si': 22.44, 'H': 1.88, 'O': 49.0}
	Found duplicates of "Schwertmannite", with these properties :
			Density 3.88, Hardness 3.0, Elements {'Fe': 57.81, 'H': 0.78, 'S': 4.15, 'O': 37.26}
			Density 3.88, Hardness 3.0, Elements {'Fe': 57.81, 'H': 0.78, 'S': 4.15, 'O': 37.26}
	Found duplicates of "Abswurmbachite", with these properties :
			Density 4.96, Hardness 6.5, Elements {'Mn': 50.88, 'Al': 0.44, 'Fe': 3.63, 'Cu': 9.29, 'Si': 4.56, 'O': 31.2}
			Density 4.96, Hardness 6.5, Elements {'Mn': 50.88, 'Al': 0.44, 'Fe': 3.63, 'Cu': 9.29, 'Si': 4.56, 'O': 31.2}
	Found duplicates of "Bystrite", with these properties :
			Density 2.43, Hardness 5.0, Elements {'K': 6.36, 'Na': 10.8, 'Ca': 3.62, 'Al': 14.14, 'Si': 15.73, 'H': 0.18, 'S': 13.04, 'O': 36.13}
			Density 2.43, Hardness 5.0, Elements {'K': 6.36, 'Na': 10.8, 'Ca': 3.62, 'Al': 14.14, 'Si': 15.73, 'H': 0.18, 'S': 13.04, 'O': 36.13}
	Found duplicates of "Tounkite", with these properties :
			Density 2.557, Hardness 5.25, Elements {'K': 5.19, 'Na': 7.44, 'Ca': 7.65, 'Al': 13.22, 'Si': 14.22, 'H': 0.17, 'S': 5.32, 'Cl': 2.94, 'O': 43.83}
			Density 2.557, Hardness 5.25, Elements {'K': 5.19, 'Na': 7.44, 'Ca': 7.65, 'Al': 13.22, 'Si': 14.22, 'H': 0.17, 'S': 5.32, 'Cl': 2.94, 'O': 43.83}
	Found duplicates of "Tooeleite", with these properties :
			Density 4.238, Hardness 3.0, Elements {'Fe': 30.56, 'As': 25.35, 'H': 1.12, 'S': 3.0, 'O': 39.97}
			Density 4.238, Hardness 3.0, Elements {'Fe': 30.56, 'As': 25.35, 'H': 1.12, 'S': 3.0, 'O': 39.97}
	Found duplicates of "Capgaronnite", with these properties :
			Density 6.19, Hardness None, Elements {'Ag': 27.07, 'Hg': 50.34, 'S': 8.05, 'I': 3.18, 'Br': 6.02, 'Cl': 5.34}
			Density 6.19, Hardness None, Elements {'Ag': 27.07, 'Hg': 50.34, 'S': 8.05, 'I': 3.18, 'Br': 6.02, 'Cl': 5.34}
	Found duplicates of "Pitiglianoite", with these properties :
			Density 2.37, Hardness 5.0, Elements {'K': 7.36, 'Na': 12.98, 'Al': 15.23, 'Si': 15.86, 'H': 0.38, 'S': 3.02, 'O': 45.17}
			Density 2.37, Hardness 5.0, Elements {'K': 7.36, 'Na': 12.98, 'Al': 15.23, 'Si': 15.86, 'H': 0.38, 'S': 3.02, 'O': 45.17}
	Found duplicates of "Cancrisilite", with these properties :
			Density 2.4, Hardness 5.0, Elements {'Na': 16.25, 'Al': 13.62, 'Si': 19.85, 'H': 0.61, 'C': 1.21, 'O': 48.46}
			Density 2.4, Hardness 5.0, Elements {'Na': 16.25, 'Al': 13.62, 'Si': 19.85, 'H': 0.61, 'C': 1.21, 'O': 48.46}
			Density 2.4, Hardness 5.0, Elements {'Na': 16.25, 'Al': 13.62, 'Si': 19.85, 'H': 0.61, 'C': 1.21, 'O': 48.46}
	Found duplicates of "Shomiokite-Y", with these properties :
			Density 2.52, Hardness 2.5, Elements {'Na': 17.6, 'Y': 22.68, 'H': 1.54, 'C': 9.19, 'O': 48.98}
			Density 2.52, Hardness 2.5, Elements {'Na': 17.6, 'Y': 22.68, 'H': 1.54, 'C': 9.19, 'O': 48.98}
	Found duplicates of "Saliotite", with these properties :
			Density 2.75, Hardness 2.5, Elements {'Na': 2.54, 'Li': 0.77, 'Al': 23.87, 'Si': 18.63, 'H': 1.11, 'O': 53.07}
			Density 2.75, Hardness 2.5, Elements {'Na': 2.54, 'Li': 0.77, 'Al': 23.87, 'Si': 18.63, 'H': 1.11, 'O': 53.07}
	Found duplicates of "Polyphite-VIII", with these properties :
			Density 3.07, Hardness 5.0, Elements {'Na': 21.88, 'Ca': 6.73, 'Mg': 1.36, 'Ti': 8.04, 'Mn': 3.08, 'Si': 6.29, 'P': 10.4, 'O': 35.83, 'F': 6.38}
			Density 3.07, Hardness 5.0, Elements {'Na': 21.88, 'Ca': 6.73, 'Mg': 1.36, 'Ti': 8.04, 'Mn': 3.08, 'Si': 6.29, 'P': 10.4, 'O': 35.83, 'F': 6.38}
	Found duplicates of "Quadruphite-VIII", with these properties :
			Density 3.12, Hardness 5.0, Elements {'Na': 23.06, 'Ca': 2.87, 'Mg': 1.74, 'Ti': 13.72, 'Si': 8.05, 'P': 8.88, 'O': 38.97, 'F': 2.72}
			Density 3.12, Hardness 5.0, Elements {'Na': 23.06, 'Ca': 2.87, 'Mg': 1.74, 'Ti': 13.72, 'Si': 8.05, 'P': 8.88, 'O': 38.97, 'F': 2.72}
	Found duplicates of "Tvedalite", with these properties :
			Density 2.541, Hardness 4.5, Elements {'Ca': 15.72, 'Mn': 7.18, 'Be': 3.54, 'Si': 22.03, 'H': 1.32, 'O': 50.21}
			Density 2.541, Hardness 4.5, Elements {'Ca': 15.72, 'Mn': 7.18, 'Be': 3.54, 'Si': 22.03, 'H': 1.32, 'O': 50.21}
	Found duplicates of "Silinaite", with these properties :
			Density 2.24, Hardness 4.5, Elements {'Na': 11.37, 'Li': 3.43, 'Si': 27.79, 'H': 1.99, 'O': 55.41}
			Density 2.24, Hardness 4.5, Elements {'Na': 11.37, 'Li': 3.43, 'Si': 27.79, 'H': 1.99, 'O': 55.41}
	Found duplicates of "Zenzenite", with these properties :
			Density 6.83, Hardness 5.7, Elements {'Mn': 20.68, 'Fe': 10.51, 'Pb': 49.17, 'O': 19.64}
			Density 6.83, Hardness 5.7, Elements {'Mn': 20.68, 'Fe': 10.51, 'Pb': 49.17, 'O': 19.64}
	Found duplicates of "Rimkorolgite", with these properties :
			Density 2.67, Hardness 3.0, Elements {'Ba': 17.54, 'Mg': 15.52, 'P': 15.83, 'H': 2.06, 'O': 49.05}
			Density 2.67, Hardness 3.0, Elements {'Ba': 17.54, 'Mg': 15.52, 'P': 15.83, 'H': 2.06, 'O': 49.05}
	Found duplicates of "Ashburtonite", with these properties :
			Density 4.69, Hardness None, Elements {'Cu': 14.64, 'Si': 6.47, 'H': 0.52, 'Pb': 47.75, 'C': 2.77, 'Cl': 2.04, 'O': 25.81}
			Density 4.69, Hardness None, Elements {'Cu': 14.64, 'Si': 6.47, 'H': 0.52, 'Pb': 47.75, 'C': 2.77, 'Cl': 2.04, 'O': 25.81}
	Found duplicates of "Camerolaite", with these properties :
			Density 3.1, Hardness None, Elements {'Al': 7.31, 'Cu': 34.43, 'Sb': 12.37, 'H': 2.01, 'C': 1.63, 'S': 1.09, 'O': 41.17}
			Density 3.1, Hardness None, Elements {'Al': 7.31, 'Cu': 34.43, 'Sb': 12.37, 'H': 2.01, 'C': 1.63, 'S': 1.09, 'O': 41.17}
	Found duplicates of "Deloryite", with these properties :
			Density 4.9, Hardness 4.0, Elements {'U': 25.16, 'Cu': 26.87, 'Mo': 20.28, 'H': 0.64, 'O': 27.06}
			Density 4.9, Hardness 4.0, Elements {'U': 25.16, 'Cu': 26.87, 'Mo': 20.28, 'H': 0.64, 'O': 27.06}
	Found duplicates of "Cianciulliite", with these properties :
			Density 2.87, Hardness 2.0, Elements {'Mg': 7.7, 'Mn': 17.39, 'Zn': 27.6, 'H': 3.4, 'O': 43.9}
			Density 2.87, Hardness 2.0, Elements {'Mg': 7.7, 'Mn': 17.39, 'Zn': 27.6, 'H': 3.4, 'O': 43.9}
	Found duplicates of "Clinomimetite", with these properties :
			Density 7.36, Hardness 3.75, Elements {'As': 15.1, 'Pb': 69.61, 'Cl': 2.38, 'O': 12.9}
			Density 7.36, Hardness 3.75, Elements {'As': 15.1, 'Pb': 69.61, 'Cl': 2.38, 'O': 12.9}
	Found duplicates of "Uranopolycrase", with these properties :
			Density 5.75, Hardness 5.5, Elements {'Y': 6.42, 'Th': 5.58, 'U': 34.35, 'Ta': 4.35, 'Ti': 17.27, 'Nb': 8.94, 'O': 23.09}
			Density 5.75, Hardness 5.5, Elements {'Y': 6.42, 'Th': 5.58, 'U': 34.35, 'Ta': 4.35, 'Ti': 17.27, 'Nb': 8.94, 'O': 23.09}
	Found duplicates of "Weinebeneite", with these properties :
			Density 2.15, Hardness 3.5, Elements {'Ca': 11.04, 'Be': 7.45, 'P': 17.06, 'H': 2.78, 'O': 61.68}
			Density 2.15, Hardness 3.5, Elements {'Ca': 11.04, 'Be': 7.45, 'P': 17.06, 'H': 2.78, 'O': 61.68}
	Found duplicates of "Yanomamite", with these properties :
			Density 3.87, Hardness 5.75, Elements {'In': 39.62, 'As': 25.86, 'H': 1.39, 'O': 33.13}
			Density 3.87, Hardness 5.75, Elements {'In': 39.62, 'As': 25.86, 'H': 1.39, 'O': 33.13}
	Found duplicates of "Quadridavyne", with these properties :
			Density 2.335, Hardness 5.0, Elements {'K': 4.96, 'Na': 8.34, 'Ca': 7.99, 'Al': 14.67, 'Si': 15.28, 'S': 0.58, 'Cl': 12.21, 'O': 35.97}
			Density 2.335, Hardness 5.0, Elements {'K': 4.96, 'Na': 8.34, 'Ca': 7.99, 'Al': 14.67, 'Si': 15.28, 'S': 0.58, 'Cl': 12.21, 'O': 35.97}
	Found duplicates of "Ferrisurite", with these properties :
			Density 4.0, Hardness 2.25, Elements {'Na': 0.25, 'Al': 1.77, 'Fe': 7.94, 'Cu': 4.17, 'Si': 12.28, 'H': 0.39, 'Pb': 38.5, 'C': 2.23, 'O': 31.65, 'F': 0.83}
			Density 4.0, Hardness 2.25, Elements {'Na': 0.25, 'Al': 1.77, 'Fe': 7.94, 'Cu': 4.17, 'Si': 12.28, 'H': 0.39, 'Pb': 38.5, 'C': 2.23, 'O': 31.65, 'F': 0.83}
	Found duplicates of "Bellbergite", with these properties :
			Density 2.2, Hardness 5.0, Elements {'K': 1.58, 'Ba': 1.28, 'Na': 0.5, 'Sr': 6.54, 'Ca': 6.61, 'Al': 15.11, 'Si': 15.73, 'H': 1.88, 'O': 50.77}
			Density 2.2, Hardness 5.0, Elements {'K': 1.58, 'Ba': 1.28, 'Na': 0.5, 'Sr': 6.54, 'Ca': 6.61, 'Al': 15.11, 'Si': 15.73, 'H': 1.88, 'O': 50.77}
	Found duplicates of "Deanesmithite", with these properties :
			Density None, Hardness 4.75, Elements {'Cr': 4.34, 'Hg': 83.64, 'S': 5.35, 'O': 6.67}
			Density None, Hardness 4.75, Elements {'Cr': 4.34, 'Hg': 83.64, 'S': 5.35, 'O': 6.67}
	Found duplicates of "Bismutocolumbite", with these properties :
			Density 7.56, Hardness 5.5, Elements {'Ta': 11.66, 'Nb': 17.96, 'Bi': 53.88, 'O': 16.5}
			Density 7.56, Hardness 5.5, Elements {'Ta': 11.66, 'Nb': 17.96, 'Bi': 53.88, 'O': 16.5}
	Found duplicates of "Reppiaite", with these properties :
			Density None, Hardness None, Elements {'Mn': 47.57, 'V': 15.88, 'As': 2.6, 'H': 0.7, 'O': 33.25}
			Density None, Hardness None, Elements {'Mn': 47.57, 'V': 15.88, 'As': 2.6, 'H': 0.7, 'O': 33.25}
	Found duplicates of "Walthierite", with these properties :
			Density None, Hardness 3.5, Elements {'Ba': 15.47, 'Al': 18.24, 'H': 1.36, 'S': 14.45, 'O': 50.47}
			Density None, Hardness 3.5, Elements {'Ba': 15.47, 'Al': 18.24, 'H': 1.36, 'S': 14.45, 'O': 50.47}
	Found duplicates of "Vistepite", with these properties :
			Density 3.67, Hardness 4.5, Elements {'Mn': 28.82, 'Si': 14.73, 'Sn': 15.57, 'B': 2.84, 'H': 0.26, 'O': 37.77}
			Density 3.67, Hardness 4.5, Elements {'Mn': 28.82, 'Si': 14.73, 'Sn': 15.57, 'B': 2.84, 'H': 0.26, 'O': 37.77}
	Found duplicates of "Tiettaite", with these properties :
			Density 2.42, Hardness 3.0, Elements {'K': 13.24, 'Na': 11.12, 'Ti': 2.32, 'Fe': 2.7, 'Si': 21.74, 'H': 1.66, 'O': 47.22}
			Density 2.42, Hardness 3.0, Elements {'K': 13.24, 'Na': 11.12, 'Ti': 2.32, 'Fe': 2.7, 'Si': 21.74, 'H': 1.66, 'O': 47.22}
	Found duplicates of "Ershovite", with these properties :
			Density 2.72, Hardness 2.75, Elements {'K': 11.47, 'Na': 9.22, 'Ti': 1.87, 'Mn': 3.76, 'Fe': 4.92, 'Si': 21.98, 'H': 1.38, 'O': 45.39}
			Density 2.72, Hardness 2.75, Elements {'K': 11.47, 'Na': 9.22, 'Ti': 1.87, 'Mn': 3.76, 'Fe': 4.92, 'Si': 21.98, 'H': 1.38, 'O': 45.39}
	Found duplicates of "Segnitite", with these properties :
			Density None, Hardness 4.0, Elements {'Fe': 22.17, 'As': 19.83, 'H': 0.93, 'Pb': 27.42, 'O': 29.64}
			Density None, Hardness 4.0, Elements {'Fe': 22.17, 'As': 19.83, 'H': 0.93, 'Pb': 27.42, 'O': 29.64}
	Found duplicates of "Trembathite", with these properties :
			Density 3.09, Hardness 7.0, Elements {'Mg': 13.16, 'Fe': 10.08, 'B': 18.2, 'Cl': 8.53, 'O': 50.04}
			Density 3.09, Hardness 7.0, Elements {'Mg': 13.16, 'Fe': 10.08, 'B': 18.2, 'Cl': 8.53, 'O': 50.04}
	Found duplicates of "Fetiasite", with these properties :
			Density 4.6, Hardness 5.0, Elements {'Ti': 3.36, 'Fe': 35.31, 'As': 35.09, 'O': 26.23}
			Density 4.6, Hardness 5.0, Elements {'Ti': 3.36, 'Fe': 35.31, 'As': 35.09, 'O': 26.23}
	Found duplicates of "Swaknoite", with these properties :
			Density 1.91, Hardness 1.75, Elements {'Ca': 14.01, 'P': 21.65, 'H': 4.2299999999999995, 'N': 9.79, 'O': 50.33}
			Density 1.91, Hardness 1.75, Elements {'Ca': 14.01, 'P': 21.65, 'H': 4.2299999999999995, 'N': 9.79, 'O': 50.33}
	Found duplicates of "Coquandite", with these properties :
			Density None, Hardness 3.5, Elements {'Sb': 75.11, 'H': 0.21, 'S': 3.3, 'O': 21.39}
			Density None, Hardness 3.5, Elements {'Sb': 75.11, 'H': 0.21, 'S': 3.3, 'O': 21.39}
	Found duplicates of "Watanabeite", with these properties :
			Density 4.66, Hardness 4.25, Elements {'Cu': 42.9, 'Sb': 12.33, 'As': 17.7, 'S': 27.06}
			Density 4.66, Hardness 4.25, Elements {'Cu': 42.9, 'Sb': 12.33, 'As': 17.7, 'S': 27.06}
	Found duplicates of "Theresemagnanite", with these properties :
			Density 2.52, Hardness 1.75, Elements {'Zn': 16.08, 'Co': 21.74, 'Ni': 7.22, 'H': 2.97, 'S': 3.94, 'Cl': 8.72, 'O': 39.34}
			Density 2.52, Hardness 1.75, Elements {'Zn': 16.08, 'Co': 21.74, 'Ni': 7.22, 'H': 2.97, 'S': 3.94, 'Cl': 8.72, 'O': 39.34}
	Found duplicates of "Bottinoite", with these properties :
			Density 2.83, Hardness 3.5, Elements {'Ni': 9.55, 'Sb': 39.63, 'H': 3.94, 'O': 46.88}
			Density 2.83, Hardness 3.5, Elements {'Ni': 9.55, 'Sb': 39.63, 'H': 3.94, 'O': 46.88}
	Found duplicates of "Vonbezingite", with these properties :
			Density 2.82, Hardness 4.0, Elements {'Ca': 25.06, 'Cu': 19.87, 'H': 1.68, 'S': 10.03, 'O': 43.36}
			Density 2.82, Hardness 4.0, Elements {'Ca': 25.06, 'Cu': 19.87, 'H': 1.68, 'S': 10.03, 'O': 43.36}
	Found duplicates of "Borodaevite", with these properties :
			Density 7.9, Hardness 3.5, Elements {'Ag': 19.71, 'Bi': 51.54, 'Sb': 10.01, 'S': 18.75}
			Density 7.9, Hardness 3.5, Elements {'Ag': 19.71, 'Bi': 51.54, 'Sb': 10.01, 'S': 18.75}
	Found duplicates of "Tsaregorodtsevite", with these properties :
			Density 2.04, Hardness 6.0, Elements {'Al': 6.22, 'Si': 32.39, 'H': 2.79, 'C': 11.08, 'N': 3.23, 'O': 44.28}
			Density 2.04, Hardness 6.0, Elements {'Al': 6.22, 'Si': 32.39, 'H': 2.79, 'C': 11.08, 'N': 3.23, 'O': 44.28}
	Found duplicates of "Stibiocolusite", with these properties :
			Density None, Hardness 4.25, Elements {'V': 3.15, 'Cu': 51.1, 'Sn': 2.2, 'Sb': 9.04, 'As': 2.78, 'S': 31.73}
			Density None, Hardness 4.25, Elements {'V': 3.15, 'Cu': 51.1, 'Sn': 2.2, 'Sb': 9.04, 'As': 2.78, 'S': 31.73}
	Found duplicates of "Stetefeldtite", with these properties :
			Density 5.38, Hardness 4.0, Elements {'Ag': 37.73, 'Sb': 42.59, 'H': 0.09, 'O': 19.59}
			Density 5.38, Hardness 4.0, Elements {'Ag': 37.73, 'Sb': 42.59, 'H': 0.09, 'O': 19.59}
	Found duplicates of "Stevensite", with these properties :
			Density 2.45, Hardness 4.0, Elements {'Na': 1.61, 'Ca': 1.28, 'Mg': 14.44, 'Fe': 2.37, 'Si': 23.84, 'H': 2.14, 'O': 54.32}
			Density 2.45, Hardness 4.0, Elements {'Na': 1.61, 'Ca': 1.28, 'Mg': 14.44, 'Fe': 2.37, 'Si': 23.84, 'H': 2.14, 'O': 54.32}
	Found duplicates of "Stibarsen", with these properties :
			Density 6.15, Hardness 3.5, Elements {'Sb': 61.91, 'As': 38.09}
			Density 6.15, Hardness 3.5, Elements {'Sb': 61.91, 'As': 38.09}
	Found duplicates of "Stibioclaudetite", with these properties :
			Density None, Hardness None, Elements {'Sb': 46.16, 'As': 33.89, 'O': 19.95}
			Density None, Hardness None, Elements {'Sb': 46.16, 'As': 33.89, 'O': 19.95}
	Found duplicates of "Stibnite", with these properties :
			Density 4.63, Hardness 2.0, Elements {'Sb': 71.68, 'S': 28.32}
			Density 4.63, Hardness 2.0, Elements {'Sb': 71.68, 'S': 28.32}
			Density 4.63, Hardness 2.0, Elements {'Sb': 71.68, 'S': 28.32}
			Density 4.63, Hardness 2.0, Elements {'Sb': 71.68, 'S': 28.32}
	Found duplicates of "Stilbite-Ca", with these properties :
			Density 2.15, Hardness 3.75, Elements {'Na': 0.8, 'Ca': 5.57, 'Al': 7.5, 'Si': 27.32, 'H': 2.1, 'O': 56.7}
			Density 2.15, Hardness 3.75, Elements {'Na': 0.8, 'Ca': 5.57, 'Al': 7.5, 'Si': 27.32, 'H': 2.1, 'O': 56.7}
	Found duplicates of "Stilbite-Na", with these properties :
			Density 2.15, Hardness 3.75, Elements {'Na': 2.39, 'Ca': 4.17, 'Al': 7.48, 'Si': 27.27, 'H': 2.1, 'O': 56.59}
			Density 2.15, Hardness 3.75, Elements {'Na': 2.39, 'Ca': 4.17, 'Al': 7.48, 'Si': 27.27, 'H': 2.1, 'O': 56.59}
			Density 2.15, Hardness 3.75, Elements {'Na': 2.39, 'Ca': 4.17, 'Al': 7.48, 'Si': 27.27, 'H': 2.1, 'O': 56.59}
			Density 2.15, Hardness 3.75, Elements {'Na': 2.39, 'Ca': 4.17, 'Al': 7.48, 'Si': 27.27, 'H': 2.1, 'O': 56.59}
	Found duplicates of "Stilpnomelane", with these properties :
			Density 2.86, Hardness 3.0, Elements {'K': 3.13, 'Mg': 2.73, 'Al': 4.33, 'Fe': 29.54, 'Si': 22.51, 'H': 0.57, 'O': 37.19}
			Density 2.86, Hardness 3.0, Elements {'K': 3.13, 'Mg': 2.73, 'Al': 4.33, 'Fe': 29.54, 'Si': 22.51, 'H': 0.57, 'O': 37.19}
			Density 2.86, Hardness 3.0, Elements {'K': 3.13, 'Mg': 2.73, 'Al': 4.33, 'Fe': 29.54, 'Si': 22.51, 'H': 0.57, 'O': 37.19}
	Found duplicates of "Stoppaniite", with these properties :
			Density 2.79, Hardness 7.5, Elements {'Na': 1.82, 'Mg': 1.37, 'Be': 4.54, 'Al': 1.75, 'Fe': 10.79, 'Si': 28.32, 'H': 0.34, 'O': 51.08}
			Density 2.79, Hardness 7.5, Elements {'Na': 1.82, 'Mg': 1.37, 'Be': 4.54, 'Al': 1.75, 'Fe': 10.79, 'Si': 28.32, 'H': 0.34, 'O': 51.08}
	Found duplicates of "Stornesite-Y", with these properties :
			Density None, Hardness None, Elements {'Na': 3.76, 'Sr': 0.02, 'Ca': 4.07, 'Y': 1.14, 'Yb': 0.2, 'Mg': 14.04, 'U': 0.01, 'Mn': 0.19, 'Fe': 12.14, 'Si': 0.01, 'P': 21.1, 'S': 0.02, 'O': 43.32}
			Density None, Hardness None, Elements {'Na': 3.76, 'Sr': 0.02, 'Ca': 4.07, 'Y': 1.14, 'Yb': 0.2, 'Mg': 14.04, 'U': 0.01, 'Mn': 0.19, 'Fe': 12.14, 'Si': 0.01, 'P': 21.1, 'S': 0.02, 'O': 43.32}
	Found duplicates of "Stratlingite", with these properties :
			Density 1.9, Hardness None, Elements {'Ca': 19.58, 'Al': 13.18, 'Si': 6.86, 'H': 3.69, 'O': 56.68}
			Density 1.9, Hardness None, Elements {'Ca': 19.58, 'Al': 13.18, 'Si': 6.86, 'H': 3.69, 'O': 56.68}
	Found duplicates of "Strakhovite", with these properties :
			Density 3.86, Hardness 5.5, Elements {'Ba': 34.97, 'Na': 1.95, 'Mn': 18.65, 'Si': 14.3, 'H': 0.26, 'O': 29.87}
			Density 3.86, Hardness 5.5, Elements {'Ba': 34.97, 'Na': 1.95, 'Mn': 18.65, 'Si': 14.3, 'H': 0.26, 'O': 29.87}
	Found duplicates of "Strontiochevkinite", with these properties :
			Density 5.44, Hardness 5.5, Elements {'Sr': 23.05, 'RE': 12.63, 'Zr': 4.0, 'Ti': 14.7, 'Fe': 4.9, 'Si': 9.85, 'O': 30.87}
			Density 5.44, Hardness 5.5, Elements {'Sr': 23.05, 'RE': 12.63, 'Zr': 4.0, 'Ti': 14.7, 'Fe': 4.9, 'Si': 9.85, 'O': 30.87}
	Found duplicates of "Strontioginorite", with these properties :
			Density 2.25, Hardness 2.5, Elements {'Sr': 16.13, 'Ca': 2.46, 'B': 18.57, 'H': 1.98, 'O': 60.86}
			Density 2.25, Hardness 2.5, Elements {'Sr': 16.13, 'Ca': 2.46, 'B': 18.57, 'H': 1.98, 'O': 60.86}
	Found duplicates of "Strontiojoaquinite", with these properties :
			Density 3.68, Hardness 5.5, Elements {'Ba': 21.39, 'Na': 1.79, 'Sr': 13.65, 'Ti': 7.46, 'Fe': 4.35, 'Si': 17.5, 'H': 0.24, 'O': 33.64}
			Density 3.68, Hardness 5.5, Elements {'Ba': 21.39, 'Na': 1.79, 'Sr': 13.65, 'Ti': 7.46, 'Fe': 4.35, 'Si': 17.5, 'H': 0.24, 'O': 33.64}
	Found duplicates of "Strontiomelane", with these properties :
			Density None, Hardness 4.25, Elements {'Sr': 11.19, 'Mn': 56.12, 'O': 32.69}
			Density None, Hardness 4.25, Elements {'Sr': 11.19, 'Mn': 56.12, 'O': 32.69}
	Found duplicates of "Apatite-SrOH", with these properties :
			Density 3.84, Hardness 5.0, Elements {'Sr': 38.0, 'Ca': 14.22, 'P': 14.66, 'H': 0.08, 'O': 31.54, 'F': 1.5}
			Density 3.84, Hardness 5.0, Elements {'Sr': 38.0, 'Ca': 14.22, 'P': 14.66, 'H': 0.08, 'O': 31.54, 'F': 1.5}
	Found duplicates of "Brewsterite-Sr", with these properties :
			Density 2.45, Hardness 5.0, Elements {'K': 0.06, 'Ba': 4.96, 'Sr': 9.35, 'Al': 8.36, 'Si': 25.23, 'H': 1.52, 'O': 50.52}
			Density 2.45, Hardness 5.0, Elements {'K': 0.06, 'Ba': 4.96, 'Sr': 9.35, 'Al': 8.36, 'Si': 25.23, 'H': 1.52, 'O': 50.52}
			Density 2.45, Hardness 5.0, Elements {'K': 0.06, 'Ba': 4.96, 'Sr': 9.35, 'Al': 8.36, 'Si': 25.23, 'H': 1.52, 'O': 50.52}
	Found duplicates of "Struverite", with these properties :
			Density 5.25, Hardness 6.25, Elements {'Ta': 30.83, 'Ti': 24.48, 'Nb': 7.92, 'Fe': 9.52, 'O': 27.26}
			Density 5.25, Hardness 6.25, Elements {'Ta': 30.83, 'Ti': 24.48, 'Nb': 7.92, 'Fe': 9.52, 'O': 27.26}
	Found duplicates of "Struvite-K", with these properties :
			Density None, Hardness None, Elements {'K': 14.67, 'Mg': 9.12, 'P': 11.62, 'H': 4.54, 'O': 60.04}
			Density None, Hardness None, Elements {'K': 14.67, 'Mg': 9.12, 'P': 11.62, 'H': 4.54, 'O': 60.04}
			Density None, Hardness None, Elements {'K': 14.67, 'Mg': 9.12, 'P': 11.62, 'H': 4.54, 'O': 60.04}
	Found duplicates of "Studenitsite", with these properties :
			Density 2.31, Hardness 5.75, Elements {'Na': 4.35, 'Ca': 15.17, 'B': 18.41, 'H': 1.53, 'O': 60.55}
			Density 2.31, Hardness 5.75, Elements {'Na': 4.35, 'Ca': 15.17, 'B': 18.41, 'H': 1.53, 'O': 60.55}
	Found duplicates of "Stutzite", with these properties :
			Density 8.0, Hardness 3.5, Elements {'Ag': 56.98, 'Te': 43.02}
			Density 8.0, Hardness 3.5, Elements {'Ag': 56.98, 'Te': 43.02}
	Found duplicates of "Fibroferrite", with these properties :
			Density 1.9, Hardness 2.0, Elements {'Fe': 21.56, 'H': 4.28, 'S': 12.38, 'O': 61.78}
			Density 1.9, Hardness 2.0, Elements {'Fe': 21.56, 'H': 4.28, 'S': 12.38, 'O': 61.78}
	Found duplicates of "Amber", with these properties :
			Density 1.1, Hardness 2.25, Elements {'H': 11.18, 'C': 79.94, 'O': 8.87}
			Density 1.1, Hardness 2.25, Elements {'H': 11.18, 'C': 79.94, 'O': 8.87}
			Density 1.1, Hardness 2.25, Elements {'H': 11.18, 'C': 79.94, 'O': 8.87}
			Density 1.1, Hardness 2.25, Elements {'H': 11.18, 'C': 79.94, 'O': 8.87}
			Density 1.1, Hardness 2.25, Elements {'H': 11.18, 'C': 79.94, 'O': 8.87}
	Found duplicates of "Sudovikovite", with these properties :
			Density 9.66, Hardness 2.25, Elements {'Pt': 55.26, 'Se': 44.74}
			Density 9.66, Hardness 2.25, Elements {'Pt': 55.26, 'Se': 44.74}
	Found duplicates of "Sugakiite", with these properties :
			Density None, Hardness 2.5, Elements {'Fe': 43.47, 'Co': 0.15, 'Cu': 7.0, 'Ni': 16.19, 'S': 33.19}
			Density None, Hardness 2.5, Elements {'Fe': 43.47, 'Co': 0.15, 'Cu': 7.0, 'Ni': 16.19, 'S': 33.19}
	Found duplicates of "Stannomicrolite", with these properties :
			Density 8.34, Hardness 5.0, Elements {'Ta': 34.06, 'Ti': 0.75, 'Mn': 0.86, 'Nb': 5.83, 'Fe': 1.75, 'Sn': 39.11, 'H': 0.06, 'O': 17.57}
			Density 8.34, Hardness 5.0, Elements {'Ta': 34.06, 'Ti': 0.75, 'Mn': 0.86, 'Nb': 5.83, 'Fe': 1.75, 'Sn': 39.11, 'H': 0.06, 'O': 17.57}
	Found duplicates of "Vishnevite", with these properties :
			Density 2.37, Hardness 5.5, Elements {'K': 3.75, 'Na': 14.35, 'Ca': 0.38, 'Al': 15.03, 'Si': 16.72, 'H': 0.27, 'C': 0.46, 'S': 2.16, 'Cl': 1.7, 'O': 45.17}
			Density 2.37, Hardness 5.5, Elements {'K': 3.75, 'Na': 14.35, 'Ca': 0.38, 'Al': 15.03, 'Si': 16.72, 'H': 0.27, 'C': 0.46, 'S': 2.16, 'Cl': 1.7, 'O': 45.17}
	Found duplicates of "Sulfur", with these properties :
			Density 2.06, Hardness 2.0, Elements {'S': 100.0}
			Density 2.06, Hardness 2.0, Elements {'S': 100.0}
			Density 2.06, Hardness 2.0, Elements {'S': 100.0}
	Found duplicates of "Bowieite", with these properties :
			Density None, Hardness 7.0, Elements {'Ir': 29.84, 'Pt': 10.09, 'Rh': 31.95, 'S': 28.12}
			Density None, Hardness 7.0, Elements {'Ir': 29.84, 'Pt': 10.09, 'Rh': 31.95, 'S': 28.12}
	Found duplicates of "Suredaite", with these properties :
			Density 5.71, Hardness 2.75, Elements {'Ag': 1.38, 'Sn': 30.41, 'As': 1.92, 'Pb': 42.46, 'S': 23.82}
			Density 5.71, Hardness 2.75, Elements {'Ag': 1.38, 'Sn': 30.41, 'As': 1.92, 'Pb': 42.46, 'S': 23.82}
	Found duplicates of "Svenekite", with these properties :
			Density None, Hardness None, Elements {'Ca': 12.53, 'As': 46.84, 'H': 0.63, 'O': 40.01}
			Density None, Hardness None, Elements {'Ca': 12.53, 'As': 46.84, 'H': 0.63, 'O': 40.01}
	Found duplicates of "Dachiardite-Na", with these properties :
			Density 2.16, Hardness 4.5, Elements {'K': 1.53, 'Ba': 0.08, 'Na': 3.29, 'Ca': 1.17, 'Mg': 0.05, 'Al': 7.25, 'Fe': 0.34, 'Si': 30.45, 'H': 1.5, 'O': 54.34}
			Density 2.16, Hardness 4.5, Elements {'K': 1.53, 'Ba': 0.08, 'Na': 3.29, 'Ca': 1.17, 'Mg': 0.05, 'Al': 7.25, 'Fe': 0.34, 'Si': 30.45, 'H': 1.5, 'O': 54.34}
			Density 2.16, Hardness 4.5, Elements {'K': 1.53, 'Ba': 0.08, 'Na': 3.29, 'Ca': 1.17, 'Mg': 0.05, 'Al': 7.25, 'Fe': 0.34, 'Si': 30.45, 'H': 1.5, 'O': 54.34}
			Density 2.16, Hardness 4.5, Elements {'K': 1.53, 'Ba': 0.08, 'Na': 3.29, 'Ca': 1.17, 'Mg': 0.05, 'Al': 7.25, 'Fe': 0.34, 'Si': 30.45, 'H': 1.5, 'O': 54.34}
	Found duplicates of "Corrensite", with these properties :
			Density None, Hardness 1.5, Elements {'K': 0.64, 'Na': 0.37, 'Ca': 1.96, 'Mg': 9.9, 'Al': 6.6, 'Fe': 13.65, 'Si': 13.73, 'H': 2.3, 'O': 50.85}
			Density None, Hardness 1.5, Elements {'K': 0.64, 'Na': 0.37, 'Ca': 1.96, 'Mg': 9.9, 'Al': 6.6, 'Fe': 13.65, 'Si': 13.73, 'H': 2.3, 'O': 50.85}
	Found duplicates of "Symesite", with these properties :
			Density 7.3, Hardness 4.0, Elements {'H': 0.08, 'Pb': 84.92, 'S': 1.31, 'Cl': 5.81, 'O': 7.87}
			Density 7.3, Hardness 4.0, Elements {'H': 0.08, 'Pb': 84.92, 'S': 1.31, 'Cl': 5.81, 'O': 7.87}
	Found duplicates of "Synchysite-Y", with these properties :
			Density 3.99, Hardness 6.25, Elements {'Ca': 14.95, 'Y': 33.17, 'C': 8.96, 'O': 35.82, 'F': 7.09}
			Density 3.99, Hardness 6.25, Elements {'Ca': 14.95, 'Y': 33.17, 'C': 8.96, 'O': 35.82, 'F': 7.09}
	Found duplicates of "Szaibelyite", with these properties :
			Density 2.67, Hardness 3.25, Elements {'Mg': 28.5, 'Fe': 0.66, 'B': 12.8, 'H': 1.19, 'O': 56.85}
			Density 2.67, Hardness 3.25, Elements {'Mg': 28.5, 'Fe': 0.66, 'B': 12.8, 'H': 1.19, 'O': 56.85}
			Density 2.67, Hardness 3.25, Elements {'Mg': 28.5, 'Fe': 0.66, 'B': 12.8, 'H': 1.19, 'O': 56.85}
	Found duplicates of "Szenicsite", with these properties :
			Density 4.26, Hardness 3.75, Elements {'Cu': 45.54, 'Mo': 22.92, 'H': 0.96, 'O': 30.58}
			Density 4.26, Hardness 3.75, Elements {'Cu': 45.54, 'Mo': 22.92, 'H': 0.96, 'O': 30.58}
	Found duplicates of "Tadzhikite-Ce", with these properties :
			Density 3.79, Hardness 6.0, Elements {'Ca': 16.05, 'Ce': 21.04, 'Y': 4.45, 'Ti': 2.88, 'Al': 0.81, 'Fe': 0.56, 'Si': 11.25, 'B': 4.33, 'H': 0.2, 'O': 38.44}
			Density 3.79, Hardness 6.0, Elements {'Ca': 16.05, 'Ce': 21.04, 'Y': 4.45, 'Ti': 2.88, 'Al': 0.81, 'Fe': 0.56, 'Si': 11.25, 'B': 4.33, 'H': 0.2, 'O': 38.44}
			Density 3.79, Hardness 6.0, Elements {'Ca': 16.05, 'Ce': 21.04, 'Y': 4.45, 'Ti': 2.88, 'Al': 0.81, 'Fe': 0.56, 'Si': 11.25, 'B': 4.33, 'H': 0.2, 'O': 38.44}
	Found duplicates of "Tainiolite", with these properties :
			Density 2.9, Hardness 2.75, Elements {'K': 9.65, 'Li': 1.71, 'Mg': 12.0, 'Si': 27.74, 'O': 39.51, 'F': 9.38}
			Density 2.9, Hardness 2.75, Elements {'K': 9.65, 'Li': 1.71, 'Mg': 12.0, 'Si': 27.74, 'O': 39.51, 'F': 9.38}
	Found duplicates of "Taenite", with these properties :
			Density 8.01, Hardness 5.25, Elements {'Fe': 79.19, 'Ni': 20.81}
			Density 8.01, Hardness 5.25, Elements {'Fe': 79.19, 'Ni': 20.81}
	Found duplicates of "Takedaite", with these properties :
			Density 3.1, Hardness 4.5, Elements {'Ca': 50.55, 'B': 9.09, 'O': 40.36}
			Density 3.1, Hardness 4.5, Elements {'Ca': 50.55, 'B': 9.09, 'O': 40.36}
	Found duplicates of "Takovite", with these properties :
			Density 2.798, Hardness 2.0, Elements {'Al': 6.75, 'Ni': 44.04, 'H': 3.06, 'C': 1.13, 'O': 45.02}
			Density 2.798, Hardness 2.0, Elements {'Al': 6.75, 'Ni': 44.04, 'H': 3.06, 'C': 1.13, 'O': 45.02}
	Found duplicates of "Tamaite", with these properties :
			Density 2.85, Hardness 4.0, Elements {'K': 0.69, 'Ba': 1.85, 'Na': 0.26, 'Ca': 1.37, 'Mn': 27.34, 'Al': 4.03, 'Si': 19.1, 'H': 1.23, 'O': 44.13}
			Density 2.85, Hardness 4.0, Elements {'K': 0.69, 'Ba': 1.85, 'Na': 0.26, 'Ca': 1.37, 'Mn': 27.34, 'Al': 4.03, 'Si': 19.1, 'H': 1.23, 'O': 44.13}
	Found duplicates of "Tanohataite", with these properties :
			Density None, Hardness None, Elements {'Li': 2.01, 'Mn': 31.75, 'Si': 24.35, 'H': 0.29, 'O': 41.61}
			Density None, Hardness None, Elements {'Li': 2.01, 'Mn': 31.75, 'Si': 24.35, 'H': 0.29, 'O': 41.61}
	Found duplicates of "Tantalaeschynite-Y", with these properties :
			Density 5.94, Hardness 5.75, Elements {'Ca': 0.8, 'Ce': 8.41, 'Y': 10.67, 'Ta': 54.31, 'Ti': 2.87, 'Nb': 3.72, 'O': 19.21}
			Density 5.94, Hardness 5.75, Elements {'Ca': 0.8, 'Ce': 8.41, 'Y': 10.67, 'Ta': 54.31, 'Ti': 2.87, 'Nb': 3.72, 'O': 19.21}
	Found duplicates of "Tantalite-Fe", with these properties :
			Density 8.2, Hardness 6.25, Elements {'Ta': 70.44, 'Fe': 10.87, 'O': 18.69}
			Density 8.2, Hardness 6.25, Elements {'Ta': 70.44, 'Fe': 10.87, 'O': 18.69}
			Density 8.2, Hardness 6.25, Elements {'Ta': 70.44, 'Fe': 10.87, 'O': 18.69}
	Found duplicates of "Tanteuxenite-Y", with these properties :
			Density 5.65, Hardness 5.5, Elements {'Ca': 1.67, 'Ce': 2.91, 'Y': 12.94, 'Ta': 52.69, 'Ti': 1.99, 'Nb': 7.73, 'H': 0.1, 'O': 19.96}
			Density 5.65, Hardness 5.5, Elements {'Ca': 1.67, 'Ce': 2.91, 'Y': 12.94, 'Ta': 52.69, 'Ti': 1.99, 'Nb': 7.73, 'H': 0.1, 'O': 19.96}
	Found duplicates of "Zoisite", with these properties :
			Density 3.3, Hardness 6.5, Elements {'Ca': 17.64, 'Al': 17.82, 'Si': 18.54, 'H': 0.22, 'O': 45.78}
			Density 3.3, Hardness 6.5, Elements {'Ca': 17.64, 'Al': 17.82, 'Si': 18.54, 'H': 0.22, 'O': 45.78}
			Density 3.3, Hardness 6.5, Elements {'Ca': 17.64, 'Al': 17.82, 'Si': 18.54, 'H': 0.22, 'O': 45.78}
			Density 3.3, Hardness 6.5, Elements {'Ca': 17.64, 'Al': 17.82, 'Si': 18.54, 'H': 0.22, 'O': 45.78}
	Found duplicates of "Tapiolite-Fe", with these properties :
			Density 7.82, Hardness 6.0, Elements {'Ta': 68.76, 'Mn': 3.3, 'Nb': 0.93, 'Fe': 7.82, 'O': 19.2}
			Density 7.82, Hardness 6.0, Elements {'Ta': 68.76, 'Mn': 3.3, 'Nb': 0.93, 'Fe': 7.82, 'O': 19.2}
	Found duplicates of "Tarbuttite", with these properties :
			Density 4.14, Hardness 4.0, Elements {'Zn': 53.87, 'P': 12.76, 'H': 0.42, 'O': 32.95}
			Density 4.14, Hardness 4.0, Elements {'Zn': 53.87, 'P': 12.76, 'H': 0.42, 'O': 32.95}
	Found duplicates of "Tarkianite", with these properties :
			Density None, Hardness 5.75, Elements {'Fe': 0.59, 'Cu': 5.67, 'Re': 54.73, 'Mo': 12.09, 'S': 26.93}
			Density None, Hardness 5.75, Elements {'Fe': 0.59, 'Cu': 5.67, 'Re': 54.73, 'Mo': 12.09, 'S': 26.93}
	Found duplicates of "Taseqite", with these properties :
			Density 3.24, Hardness None, Elements {'K': 0.2, 'Na': 5.95, 'Sr': 12.31, 'Ca': 6.09, 'Ce': 0.08, 'Y': 0.24, 'Hf': 0.26, 'Zr': 7.61, 'Ta': 0.21, 'Mn': 2.44, 'Nb': 3.19, 'Fe': 3.17, 'Si': 20.26, 'Sn': 0.1, 'H': 0.07, 'Cl': 1.99, 'O': 35.84}
			Density 3.24, Hardness None, Elements {'K': 0.2, 'Na': 5.95, 'Sr': 12.31, 'Ca': 6.09, 'Ce': 0.08, 'Y': 0.24, 'Hf': 0.26, 'Zr': 7.61, 'Ta': 0.21, 'Mn': 2.44, 'Nb': 3.19, 'Fe': 3.17, 'Si': 20.26, 'Sn': 0.1, 'H': 0.07, 'Cl': 1.99, 'O': 35.84}
	Found duplicates of "Tassieite", with these properties :
			Density None, Hardness None, Elements {'Na': 1.44, 'Ca': 8.22, 'Y': 0.19, 'Yb': 0.18, 'Mg': 6.59, 'Mn': 0.29, 'Fe': 19.7, 'P': 19.39, 'H': 0.42, 'S': 0.03, 'O': 43.54}
			Density None, Hardness None, Elements {'Na': 1.44, 'Ca': 8.22, 'Y': 0.19, 'Yb': 0.18, 'Mg': 6.59, 'Mn': 0.29, 'Fe': 19.7, 'P': 19.39, 'H': 0.42, 'S': 0.03, 'O': 43.54}
	Found duplicates of "Tatyanaite", with these properties :
			Density None, Hardness 3.75, Elements {'Cu': 13.42, 'Sn': 3.28, 'Pd': 25.12, 'Pt': 58.18}
			Density None, Hardness 3.75, Elements {'Cu': 13.42, 'Sn': 3.28, 'Pd': 25.12, 'Pt': 58.18}
	Found duplicates of "Arcanite", with these properties :
			Density 2.663, Hardness 2.0, Elements {'K': 44.87, 'S': 18.4, 'O': 36.73}
			Density 2.663, Hardness 2.0, Elements {'K': 44.87, 'S': 18.4, 'O': 36.73}
			Density 2.663, Hardness 2.0, Elements {'K': 44.87, 'S': 18.4, 'O': 36.73}
	Found duplicates of "Tazheranite", with these properties :
			Density 5.01, Hardness 7.5, Elements {'Ca': 10.06, 'Zr': 45.8, 'Ti': 12.02, 'O': 32.13}
			Density 5.01, Hardness 7.5, Elements {'Ca': 10.06, 'Zr': 45.8, 'Ti': 12.02, 'O': 32.13}
	Found duplicates of "Tedhadleyite", with these properties :
			Density None, Hardness 2.5, Elements {'Hg': 80.29, 'I': 10.35, 'Br': 2.96, 'Cl': 4.2, 'O': 2.19}
			Density None, Hardness 2.5, Elements {'Hg': 80.29, 'I': 10.35, 'Br': 2.96, 'Cl': 4.2, 'O': 2.19}
	Found duplicates of "Tegengrenite", with these properties :
			Density None, Hardness None, Elements {'Mg': 13.31, 'Ti': 0.87, 'Mn': 25.07, 'Zn': 2.98, 'Si': 0.77, 'Sb': 27.78, 'O': 29.21}
			Density None, Hardness None, Elements {'Mg': 13.31, 'Ti': 0.87, 'Mn': 25.07, 'Zn': 2.98, 'Si': 0.77, 'Sb': 27.78, 'O': 29.21}
	Found duplicates of "Telluronevskite", with these properties :
			Density 8.1, Hardness 3.5, Elements {'Bi': 68.56, 'Te': 14.48, 'Pb': 0.47, 'Se': 15.35, 'S': 1.15}
			Density 8.1, Hardness 3.5, Elements {'Bi': 68.56, 'Te': 14.48, 'Pb': 0.47, 'Se': 15.35, 'S': 1.15}
	Found duplicates of "Telyushenkoite", with these properties :
			Density 2.73, Hardness 6.0, Elements {'Cs': 6.4, 'K': 0.38, 'Rb': 0.06, 'Na': 10.11, 'Be': 1.28, 'Al': 3.88, 'Zn': 1.37, 'Si': 30.3, 'O': 43.36, 'F': 2.86}
			Density 2.73, Hardness 6.0, Elements {'Cs': 6.4, 'K': 0.38, 'Rb': 0.06, 'Na': 10.11, 'Be': 1.28, 'Al': 3.88, 'Zn': 1.37, 'Si': 30.3, 'O': 43.36, 'F': 2.86}
	Found duplicates of "Terlinguacreekite", with these properties :
			Density None, Hardness 2.5, Elements {'Hg': 84.77, 'Br': 1.24, 'Cl': 9.49, 'O': 4.51}
			Density None, Hardness 2.5, Elements {'Hg': 84.77, 'Br': 1.24, 'Cl': 9.49, 'O': 4.51}
	Found duplicates of "Ternesite", with these properties :
			Density 2.96, Hardness 4.5, Elements {'Ca': 41.69, 'Si': 11.69, 'S': 6.67, 'O': 39.95}
			Density 2.96, Hardness 4.5, Elements {'Ca': 41.69, 'Si': 11.69, 'S': 6.67, 'O': 39.95}
	Found duplicates of "Ternovite", with these properties :
			Density 2.95, Hardness 3.0, Elements {'Ca': 1.59, 'Mg': 2.25, 'Nb': 49.1, 'H': 2.66, 'O': 44.4}
			Density 2.95, Hardness 3.0, Elements {'Ca': 1.59, 'Mg': 2.25, 'Nb': 49.1, 'H': 2.66, 'O': 44.4}
	Found duplicates of "Terranovaite", with these properties :
			Density 2.05, Hardness None, Elements {'K': 0.14, 'Na': 1.73, 'Ca': 2.66, 'Mg': 0.09, 'Al': 5.95, 'Si': 34.12, 'H': 1.05, 'O': 54.26}
			Density 2.05, Hardness None, Elements {'K': 0.14, 'Na': 1.73, 'Ca': 2.66, 'Mg': 0.09, 'Al': 5.95, 'Si': 34.12, 'H': 1.05, 'O': 54.26}
	Found duplicates of "Tetraferriphlogopite", with these properties :
			Density None, Hardness None, Elements {'K': 8.76, 'Mg': 16.34, 'Fe': 12.52, 'Si': 18.89, 'H': 0.45, 'O': 43.04}
			Density None, Hardness None, Elements {'K': 8.76, 'Mg': 16.34, 'Fe': 12.52, 'Si': 18.89, 'H': 0.45, 'O': 43.04}
			Density None, Hardness None, Elements {'K': 8.76, 'Mg': 16.34, 'Fe': 12.52, 'Si': 18.89, 'H': 0.45, 'O': 43.04}
	Found duplicates of "Tetradymite", with these properties :
			Density 7.55, Hardness 1.75, Elements {'Bi': 59.27, 'Te': 36.19, 'S': 4.55}
			Density 7.55, Hardness 1.75, Elements {'Bi': 59.27, 'Te': 36.19, 'S': 4.55}
	Found duplicates of "Tetrarooseveltite", with these properties :
			Density None, Hardness 2.5, Elements {'Bi': 60.07, 'As': 21.54, 'O': 18.4}
			Density None, Hardness 2.5, Elements {'Bi': 60.07, 'As': 21.54, 'O': 18.4}
	Found duplicates of "Zaratite", with these properties :
			Density 2.6, Hardness 3.25, Elements {'Ni': 46.81, 'H': 3.22, 'C': 3.19, 'O': 46.79}
			Density 2.6, Hardness 3.25, Elements {'Ni': 46.81, 'H': 3.22, 'C': 3.19, 'O': 46.79}
	Found duplicates of "Theoparacelsite", with these properties :
			Density None, Hardness None, Elements {'Cu': 39.19, 'As': 30.8, 'H': 0.41, 'O': 29.6}
			Density None, Hardness None, Elements {'Cu': 39.19, 'As': 30.8, 'H': 0.41, 'O': 29.6}
	Found duplicates of "Thermessaite", with these properties :
			Density 2.79, Hardness None, Elements {'K': 30.5, 'Al': 10.94, 'S': 11.64, 'O': 23.66, 'F': 23.26}
			Density 2.79, Hardness None, Elements {'K': 30.5, 'Al': 10.94, 'S': 11.64, 'O': 23.66, 'F': 23.26}
	Found duplicates of "Thomasclarkite-Y", with these properties :
			Density 2.3, Hardness 2.5, Elements {'Na': 4.89, 'Ce': 7.46, 'RE': 26.83, 'Y': 11.83, 'H': 3.22, 'C': 3.2, 'O': 42.58}
			Density 2.3, Hardness 2.5, Elements {'Na': 4.89, 'Ce': 7.46, 'RE': 26.83, 'Y': 11.83, 'H': 3.22, 'C': 3.2, 'O': 42.58}
	Found duplicates of "Thomsonite-Ca", with these properties :
			Density 2.34, Hardness 5.25, Elements {'Na': 2.85, 'Ca': 9.94, 'Al': 16.73, 'Si': 17.41, 'H': 1.5, 'O': 51.58}
			Density 2.34, Hardness 5.25, Elements {'Na': 2.85, 'Ca': 9.94, 'Al': 16.73, 'Si': 17.41, 'H': 1.5, 'O': 51.58}
			Density 2.34, Hardness 5.25, Elements {'Na': 2.85, 'Ca': 9.94, 'Al': 16.73, 'Si': 17.41, 'H': 1.5, 'O': 51.58}
	Found duplicates of "Thomsonite-Sr", with these properties :
			Density 2.47, Hardness 5.0, Elements {'Na': 2.57, 'Sr': 13.74, 'Ca': 2.69, 'Al': 15.11, 'Si': 15.73, 'H': 1.6, 'O': 48.56}
			Density 2.47, Hardness 5.0, Elements {'Na': 2.57, 'Sr': 13.74, 'Ca': 2.69, 'Al': 15.11, 'Si': 15.73, 'H': 1.6, 'O': 48.56}
	Found duplicates of "Thorbastnasite", with these properties :
			Density 4.04, Hardness 4.25, Elements {'Ca': 5.9, 'Ce': 6.88, 'Th': 45.57, 'H': 1.19, 'C': 4.72, 'O': 28.28, 'F': 7.46}
			Density 4.04, Hardness 4.25, Elements {'Ca': 5.9, 'Ce': 6.88, 'Th': 45.57, 'H': 1.19, 'C': 4.72, 'O': 28.28, 'F': 7.46}
	Found duplicates of "Chamosite", with these properties :
			Density 3.2, Hardness 3.0, Elements {'Mg': 5.49, 'Al': 8.12, 'Fe': 29.43, 'Si': 12.69, 'H': 0.91, 'O': 43.36}
			Density 3.2, Hardness 3.0, Elements {'Mg': 5.49, 'Al': 8.12, 'Fe': 29.43, 'Si': 12.69, 'H': 0.91, 'O': 43.36}
			Density 3.2, Hardness 3.0, Elements {'Mg': 5.49, 'Al': 8.12, 'Fe': 29.43, 'Si': 12.69, 'H': 0.91, 'O': 43.36}
			Density 3.2, Hardness 3.0, Elements {'Mg': 5.49, 'Al': 8.12, 'Fe': 29.43, 'Si': 12.69, 'H': 0.91, 'O': 43.36}
			Density 3.2, Hardness 3.0, Elements {'Mg': 5.49, 'Al': 8.12, 'Fe': 29.43, 'Si': 12.69, 'H': 0.91, 'O': 43.36}
	Found duplicates of "Tillmannsite", with these properties :
			Density None, Hardness None, Elements {'V': 5.29, 'Ag': 49.83, 'Hg': 30.48, 'As': 4.18, 'O': 10.22}
			Density None, Hardness None, Elements {'V': 5.29, 'Ag': 49.83, 'Hg': 30.48, 'As': 4.18, 'O': 10.22}
	Found duplicates of "Cassiterite", with these properties :
			Density 6.9, Hardness 6.5, Elements {'Sn': 78.77, 'O': 21.23}
			Density 6.9, Hardness 6.5, Elements {'Sn': 78.77, 'O': 21.23}
			Density 6.9, Hardness 6.5, Elements {'Sn': 78.77, 'O': 21.23}
	Found duplicates of "Borax", with these properties :
			Density 1.71, Hardness 2.25, Elements {'Na': 12.06, 'B': 11.34, 'H': 5.29, 'O': 71.32}
			Density 1.71, Hardness 2.25, Elements {'Na': 12.06, 'B': 11.34, 'H': 5.29, 'O': 71.32}
	Found duplicates of "Stannite", with these properties :
			Density 4.4, Hardness 3.75, Elements {'Fe': 12.99, 'Cu': 29.56, 'Sn': 27.61, 'S': 29.83}
			Density 4.4, Hardness 3.75, Elements {'Fe': 12.99, 'Cu': 29.56, 'Sn': 27.61, 'S': 29.83}
	Found duplicates of "Tinzenite", with these properties :
			Density 3.29, Hardness 6.75, Elements {'Ca': 12.6, 'Mn': 8.64, 'Al': 9.43, 'Fe': 2.93, 'Si': 19.62, 'B': 1.89, 'H': 0.18, 'O': 44.72}
			Density 3.29, Hardness 6.75, Elements {'Ca': 12.6, 'Mn': 8.64, 'Al': 9.43, 'Fe': 2.93, 'Si': 19.62, 'B': 1.89, 'H': 0.18, 'O': 44.72}
	Found duplicates of "Tyrolite", with these properties :
			Density 3.15, Hardness 1.75, Elements {'Ca': 4.6, 'Cu': 36.45, 'As': 17.19, 'H': 1.85, 'C': 1.38, 'O': 38.54}
			Density 3.15, Hardness 1.75, Elements {'Ca': 4.6, 'Cu': 36.45, 'As': 17.19, 'H': 1.85, 'C': 1.38, 'O': 38.54}
	Found duplicates of "Tischendorfite", with these properties :
			Density None, Hardness 5.0, Elements {'Ag': 1.0, 'Hg': 25.18, 'Pd': 39.58, 'Pb': 1.93, 'Se': 32.31}
			Density None, Hardness 5.0, Elements {'Ag': 1.0, 'Hg': 25.18, 'Pd': 39.58, 'Pb': 1.93, 'Se': 32.31}
	Found duplicates of "Clinohumite", with these properties :
			Density 3.26, Hardness 6.0, Elements {'Mg': 23.6, 'Fe': 18.08, 'Si': 16.16, 'H': 0.07, 'O': 37.98, 'F': 4.1}
			Density 3.26, Hardness 6.0, Elements {'Mg': 23.6, 'Fe': 18.08, 'Si': 16.16, 'H': 0.07, 'O': 37.98, 'F': 4.1}
	Found duplicates of "Tobermorite", with these properties :
			Density 2.43, Hardness 2.5, Elements {'Ca': 24.54, 'Al': 1.92, 'Si': 21.99, 'H': 1.44, 'O': 50.11}
			Density 2.43, Hardness 2.5, Elements {'Ca': 24.54, 'Al': 1.92, 'Si': 21.99, 'H': 1.44, 'O': 50.11}
	Found duplicates of "Tokyoite", with these properties :
			Density None, Hardness 4.25, Elements {'Ba': 46.66, 'Na': 0.08, 'Sr': 0.16, 'Ca': 0.07, 'Mn': 7.88, 'Al': 0.05, 'V': 17.94, 'Fe': 1.68, 'Si': 0.05, 'H': 0.18, 'O': 25.26}
			Density None, Hardness 4.25, Elements {'Ba': 46.66, 'Na': 0.08, 'Sr': 0.16, 'Ca': 0.07, 'Mn': 7.88, 'Al': 0.05, 'V': 17.94, 'Fe': 1.68, 'Si': 0.05, 'H': 0.18, 'O': 25.26}
	Found duplicates of "Topaz", with these properties :
			Density 3.55, Hardness 8.0, Elements {'Al': 29.61, 'Si': 15.41, 'H': 0.5, 'O': 43.02, 'F': 11.47}
			Density 3.55, Hardness 8.0, Elements {'Al': 29.61, 'Si': 15.41, 'H': 0.5, 'O': 43.02, 'F': 11.47}
	Found duplicates of "Tosudite", with these properties :
			Density 2.42, Hardness 1.5, Elements {'Na': 1.18, 'Mg': 4.99, 'Al': 13.85, 'Si': 20.19, 'H': 2.28, 'O': 57.51}
			Density 2.42, Hardness 1.5, Elements {'Na': 1.18, 'Mg': 4.99, 'Al': 13.85, 'Si': 20.19, 'H': 2.28, 'O': 57.51}
			Density 2.42, Hardness 1.5, Elements {'Na': 1.18, 'Mg': 4.99, 'Al': 13.85, 'Si': 20.19, 'H': 2.28, 'O': 57.51}
	Found duplicates of "Trattnerite", with these properties :
			Density None, Hardness None, Elements {'K': 0.27, 'Na': 0.07, 'Mg': 5.88, 'Ti': 0.05, 'Mn': 0.43, 'Al': 0.11, 'Zn': 0.32, 'Fe': 12.57, 'Si': 33.13, 'O': 47.18}
			Density None, Hardness None, Elements {'K': 0.27, 'Na': 0.07, 'Mg': 5.88, 'Ti': 0.05, 'Mn': 0.43, 'Al': 0.11, 'Zn': 0.32, 'Fe': 12.57, 'Si': 33.13, 'O': 47.18}
	Found duplicates of "Tripuhyite", with these properties :
			Density 5.82, Hardness 7.0, Elements {'Fe': 23.12, 'Sb': 50.39, 'O': 26.49}
			Density 5.82, Hardness 7.0, Elements {'Fe': 23.12, 'Sb': 50.39, 'O': 26.49}
	Found duplicates of "Tritomite-Ce", with these properties :
			Density 4.2, Hardness 5.5, Elements {'Ca': 10.17, 'La': 17.63, 'Ce': 26.68, 'Y': 3.95, 'Th': 4.42, 'Si': 8.91, 'B': 0.69, 'H': 0.38, 'O': 22.34, 'F': 4.82}
			Density 4.2, Hardness 5.5, Elements {'Ca': 10.17, 'La': 17.63, 'Ce': 26.68, 'Y': 3.95, 'Th': 4.42, 'Si': 8.91, 'B': 0.69, 'H': 0.38, 'O': 22.34, 'F': 4.82}
	Found duplicates of "Tritomite-Y", with these properties :
			Density 3.22, Hardness 5.0, Elements {'Ca': 8.67, 'La': 20.03, 'Y': 25.64, 'Al': 1.17, 'Fe': 4.03, 'Si': 8.1, 'B': 1.09, 'H': 0.44, 'O': 25.37, 'F': 5.48}
			Density 3.22, Hardness 5.0, Elements {'Ca': 8.67, 'La': 20.03, 'Y': 25.64, 'Al': 1.17, 'Fe': 4.03, 'Si': 8.1, 'B': 1.09, 'H': 0.44, 'O': 25.37, 'F': 5.48}
	Found duplicates of "Trogerite", with these properties :
			Density 3.55, Hardness 2.5, Elements {'U': 49.38, 'As': 15.54, 'H': 1.88, 'O': 33.19}
			Density 3.55, Hardness 2.5, Elements {'U': 49.38, 'As': 15.54, 'H': 1.88, 'O': 33.19}
	Found duplicates of "Trona", with these properties :
			Density 2.13, Hardness 2.5, Elements {'Na': 30.51, 'H': 2.23, 'C': 10.63, 'O': 56.63}
			Density 2.13, Hardness 2.5, Elements {'Na': 30.51, 'H': 2.23, 'C': 10.63, 'O': 56.63}
	Found duplicates of "Willemite", with these properties :
			Density 4.05, Hardness 5.5, Elements {'Zn': 58.68, 'Si': 12.6, 'O': 28.72}
			Density 4.05, Hardness 5.5, Elements {'Zn': 58.68, 'Si': 12.6, 'O': 28.72}
	Found duplicates of "Trustedtite", with these properties :
			Density None, Hardness 2.5, Elements {'Ni': 35.79, 'Se': 64.21}
			Density None, Hardness 2.5, Elements {'Ni': 35.79, 'Se': 64.21}
	Found duplicates of "Chevkinite-Ce", with these properties :
			Density 4.5, Hardness 5.25, Elements {'Ca': 2.66, 'La': 16.13, 'Ce': 19.76, 'Th': 1.93, 'Mg': 0.4, 'Ti': 9.93, 'Fe': 10.66, 'Si': 9.32, 'O': 29.2}
			Density 4.5, Hardness 5.25, Elements {'Ca': 2.66, 'La': 16.13, 'Ce': 19.76, 'Th': 1.93, 'Mg': 0.4, 'Ti': 9.93, 'Fe': 10.66, 'Si': 9.32, 'O': 29.2}
	Found duplicates of "Tschermakite", with these properties :
			Density 3.24, Hardness 5.5, Elements {'Ca': 9.49, 'Mg': 8.64, 'Al': 9.59, 'Fe': 6.61, 'Si': 19.96, 'H': 0.24, 'O': 45.48}
			Density 3.24, Hardness 5.5, Elements {'Ca': 9.49, 'Mg': 8.64, 'Al': 9.59, 'Fe': 6.61, 'Si': 19.96, 'H': 0.24, 'O': 45.48}
	Found duplicates of "Tschermigite", with these properties :
			Density 1.65, Hardness 1.75, Elements {'Al': 5.95, 'H': 6.2299999999999995, 'S': 14.15, 'N': 3.09, 'O': 70.59}
			Density 1.65, Hardness 1.75, Elements {'Al': 5.95, 'H': 6.2299999999999995, 'S': 14.15, 'N': 3.09, 'O': 70.59}
			Density 1.65, Hardness 1.75, Elements {'Al': 5.95, 'H': 6.2299999999999995, 'S': 14.15, 'N': 3.09, 'O': 70.59}
	Found duplicates of "Tschortnerite", with these properties :
			Density 2.1, Hardness 4.5, Elements {'K': 0.96, 'Ba': 1.68, 'Sr': 4.3, 'Ca': 8.03, 'Al': 13.24, 'Cu': 7.79, 'Si': 13.78, 'H': 1.81, 'O': 48.41}
			Density 2.1, Hardness 4.5, Elements {'K': 0.96, 'Ba': 1.68, 'Sr': 4.3, 'Ca': 8.03, 'Al': 13.24, 'Cu': 7.79, 'Si': 13.78, 'H': 1.81, 'O': 48.41}
	Found duplicates of "Tsepinite-Ca", with these properties :
			Density 2.73, Hardness None, Elements {'K': 1.82, 'Ba': 3.09, 'Na': 0.98, 'Sr': 2.56, 'Ca': 3.74, 'Ti': 13.04, 'Nb': 6.27, 'Si': 19.05, 'H': 1.48, 'O': 47.98}
			Density 2.73, Hardness None, Elements {'K': 1.82, 'Ba': 3.09, 'Na': 0.98, 'Sr': 2.56, 'Ca': 3.74, 'Ti': 13.04, 'Nb': 6.27, 'Si': 19.05, 'H': 1.48, 'O': 47.98}
	Found duplicates of "Tsepinite-K", with these properties :
			Density 2.88, Hardness 5.0, Elements {'K': 5.47, 'Ba': 10.28, 'Na': 1.61, 'Ti': 12.93, 'Mn': 0.89, 'Nb': 4.99, 'Fe': 0.27, 'Si': 18.28, 'H': 1.16, 'O': 44.11}
			Density 2.88, Hardness 5.0, Elements {'K': 5.47, 'Ba': 10.28, 'Na': 1.61, 'Ti': 12.93, 'Mn': 0.89, 'Nb': 4.99, 'Fe': 0.27, 'Si': 18.28, 'H': 1.16, 'O': 44.11}
	Found duplicates of "Tsepinite-Na", with these properties :
			Density 2.74, Hardness 5.0, Elements {'K': 1.3, 'Ba': 2.29, 'Na': 4.03, 'Sr': 1.83, 'Ca': 0.17, 'Ti': 8.39, 'Nb': 14.34, 'Fe': 0.23, 'Si': 18.74, 'H': 1.45, 'O': 47.24}
			Density 2.74, Hardness 5.0, Elements {'K': 1.3, 'Ba': 2.29, 'Na': 4.03, 'Sr': 1.83, 'Ca': 0.17, 'Ti': 8.39, 'Nb': 14.34, 'Fe': 0.23, 'Si': 18.74, 'H': 1.45, 'O': 47.24}
	Found duplicates of "Tsepinite-Sr", with these properties :
			Density 2.67, Hardness 5.0, Elements {'K': 1.09, 'Ba': 3.83, 'Na': 0.44, 'Sr': 4.28, 'Ca': 0.63, 'Ti': 11.03, 'Nb': 11.19, 'Al': 0.09, 'Zn': 0.23, 'Fe': 0.1, 'Si': 19.51, 'H': 1.24, 'O': 46.33}
			Density 2.67, Hardness 5.0, Elements {'K': 1.09, 'Ba': 3.83, 'Na': 0.44, 'Sr': 4.28, 'Ca': 0.63, 'Ti': 11.03, 'Nb': 11.19, 'Al': 0.09, 'Zn': 0.23, 'Fe': 0.1, 'Si': 19.51, 'H': 1.24, 'O': 46.33}
	Found duplicates of "Tsugaruite", with these properties :
			Density None, Hardness 2.75, Elements {'As': 12.45, 'Pb': 68.89, 'S': 18.66}
			Density None, Hardness 2.75, Elements {'As': 12.45, 'Pb': 68.89, 'S': 18.66}
	Found duplicates of "Tsumgallite", with these properties :
			Density None, Hardness 2.0, Elements {'Zn': 0.65, 'Ga': 59.93, 'Fe': 2.79, 'Si': 0.56, 'Ge': 2.9, 'H': 1.02, 'O': 32.14}
			Density None, Hardness 2.0, Elements {'Zn': 0.65, 'Ga': 59.93, 'Fe': 2.79, 'Si': 0.56, 'Ge': 2.9, 'H': 1.02, 'O': 32.14}
	Found duplicates of "Tuite", with these properties :
			Density None, Hardness None, Elements {'Na': 2.1, 'Ca': 32.81, 'Mg': 2.14, 'Fe': 0.36, 'P': 20.41, 'O': 42.17}
			Density None, Hardness None, Elements {'Na': 2.1, 'Ca': 32.81, 'Mg': 2.14, 'Fe': 0.36, 'P': 20.41, 'O': 42.17}
	Found duplicates of "Tumchaite", with these properties :
			Density 2.78, Hardness 4.5, Elements {'Na': 9.94, 'Zr': 14.79, 'Si': 24.29, 'Sn': 5.13, 'H': 0.87, 'O': 44.97}
			Density 2.78, Hardness 4.5, Elements {'Na': 9.94, 'Zr': 14.79, 'Si': 24.29, 'Sn': 5.13, 'H': 0.87, 'O': 44.97}
	Found duplicates of "Tungstibite", with these properties :
			Density 6.69, Hardness 2.0, Elements {'Sb': 46.53, 'W': 35.13, 'O': 18.34}
			Density 6.69, Hardness 2.0, Elements {'Sb': 46.53, 'W': 35.13, 'O': 18.34}
	Found duplicates of "Turkestanite", with these properties :
			Density 3.36, Hardness 5.75, Elements {'K': 2.67, 'Na': 1.31, 'Ca': 6.84, 'Th': 21.11, 'Si': 25.55, 'H': 0.69, 'O': 41.84}
			Density 3.36, Hardness 5.75, Elements {'K': 2.67, 'Na': 1.31, 'Ca': 6.84, 'Th': 21.11, 'Si': 25.55, 'H': 0.69, 'O': 41.84}
	Found duplicates of "Turquoise", with these properties :
			Density 2.7, Hardness 5.5, Elements {'Al': 19.9, 'Cu': 7.81, 'P': 15.23, 'H': 1.98, 'O': 55.07}
			Density 2.7, Hardness 5.5, Elements {'Al': 19.9, 'Cu': 7.81, 'P': 15.23, 'H': 1.98, 'O': 55.07}
	Found duplicates of "Tuzlaite", with these properties :
			Density 2.21, Hardness 2.5, Elements {'Na': 6.9, 'Ca': 12.03, 'B': 16.22, 'H': 2.42, 'O': 62.43}
			Density 2.21, Hardness 2.5, Elements {'Na': 6.9, 'Ca': 12.03, 'B': 16.22, 'H': 2.42, 'O': 62.43}
	Found duplicates of "Fluocerite-Ce", with these properties :
			Density 6.13, Hardness 4.75, Elements {'La': 7.05, 'Ce': 64.02, 'F': 28.93}
			Density 6.13, Hardness 4.75, Elements {'La': 7.05, 'Ce': 64.02, 'F': 28.93}
	Found duplicates of "Tyuyamunite", with these properties :
			Density 3.8, Hardness 1.75, Elements {'Ca': 4.37, 'U': 51.85, 'V': 11.1, 'H': 1.32, 'O': 31.37}
			Density 3.8, Hardness 1.75, Elements {'Ca': 4.37, 'U': 51.85, 'V': 11.1, 'H': 1.32, 'O': 31.37}
	Found duplicates of "Uedaite-Ce", with these properties :
			Density None, Hardness 5.5, Elements {'Ca': 1.78, 'La': 2.61, 'Ce': 9.34, 'Pr': 1.69, 'Sm': 1.29, 'Gd': 0.54, 'Y': 0.61, 'Th': 0.4, 'Mg': 0.04, 'Mn': 4.79, 'Al': 8.72, 'Fe': 12.8, 'Si': 14.41, 'H': 0.17, 'Nd': 5.67, 'O': 35.15}
			Density None, Hardness 5.5, Elements {'Ca': 1.78, 'La': 2.61, 'Ce': 9.34, 'Pr': 1.69, 'Sm': 1.29, 'Gd': 0.54, 'Y': 0.61, 'Th': 0.4, 'Mg': 0.04, 'Mn': 4.79, 'Al': 8.72, 'Fe': 12.8, 'Si': 14.41, 'H': 0.17, 'Nd': 5.67, 'O': 35.15}
	Found duplicates of "Ulvospinel", with these properties :
			Density None, Hardness 5.75, Elements {'Ti': 21.42, 'Fe': 49.96, 'O': 28.63}
			Density None, Hardness 5.75, Elements {'Ti': 21.42, 'Fe': 49.96, 'O': 28.63}
	Found duplicates of "Ungarettiite", with these properties :
			Density 3.52, Hardness 6.0, Elements {'Na': 7.24, 'Mn': 28.84, 'Si': 23.59, 'O': 40.32}
			Density 3.52, Hardness 6.0, Elements {'Na': 7.24, 'Mn': 28.84, 'Si': 23.59, 'O': 40.32}
	Found duplicates of "Ungavaite", with these properties :
			Density None, Hardness None, Elements {'Fe': 0.13, 'Hg': 0.18, 'Bi': 0.42, 'Sb': 44.52, 'Te': 0.1, 'As': 0.2, 'Pd': 54.46}
			Density None, Hardness None, Elements {'Fe': 0.13, 'Hg': 0.18, 'Bi': 0.42, 'Sb': 44.52, 'Te': 0.1, 'As': 0.2, 'Pd': 54.46}
	Found duplicates of "Uramarsite", with these properties :
			Density 3.22, Hardness 2.5, Elements {'Na': 0.22, 'U': 51.4, 'As': 9.77, 'P': 2.58, 'H': 2.05, 'N': 1.72, 'O': 32.26}
			Density 3.22, Hardness 2.5, Elements {'Na': 0.22, 'U': 51.4, 'As': 9.77, 'P': 2.58, 'H': 2.05, 'N': 1.72, 'O': 32.26}
	Found duplicates of "Uranmicrolite", with these properties :
			Density 5.81, Hardness 5.5, Elements {'Ca': 6.91, 'U': 27.37, 'Ta': 46.81, 'Nb': 2.67, 'H': 0.14, 'O': 16.1}
			Density 5.81, Hardness 5.5, Elements {'Ca': 6.91, 'U': 27.37, 'Ta': 46.81, 'Nb': 2.67, 'H': 0.14, 'O': 16.1}
	Found duplicates of "Uranophane-beta", with these properties :
			Density 3.9, Hardness 2.75, Elements {'Ca': 5.11, 'U': 60.7, 'Si': 7.16, 'H': 0.51, 'O': 26.52}
			Density 3.9, Hardness 2.75, Elements {'Ca': 5.11, 'U': 60.7, 'Si': 7.16, 'H': 0.51, 'O': 26.52}
			Density 3.9, Hardness 2.75, Elements {'Ca': 5.11, 'U': 60.7, 'Si': 7.16, 'H': 0.51, 'O': 26.52}
			Density 3.9, Hardness 2.75, Elements {'Ca': 5.11, 'U': 60.7, 'Si': 7.16, 'H': 0.51, 'O': 26.52}
	Found duplicates of "Uranophane", with these properties :
			Density 3.9, Hardness 2.5, Elements {'Ca': 6.84, 'U': 40.59, 'Si': 9.58, 'H': 2.06, 'O': 40.93}
			Density 3.9, Hardness 2.5, Elements {'Ca': 6.84, 'U': 40.59, 'Si': 9.58, 'H': 2.06, 'O': 40.93}
	Found duplicates of "Sideronatrite", with these properties :
			Density 2.25, Hardness 1.75, Elements {'Na': 12.6, 'Fe': 15.3, 'H': 1.93, 'S': 17.57, 'O': 52.6}
			Density 2.25, Hardness 1.75, Elements {'Na': 12.6, 'Fe': 15.3, 'H': 1.93, 'S': 17.57, 'O': 52.6}
	Found duplicates of "Urusovite", with these properties :
			Density None, Hardness 4.0, Elements {'Al': 10.99, 'Cu': 25.89, 'As': 30.52, 'O': 32.59}
			Density None, Hardness 4.0, Elements {'Al': 10.99, 'Cu': 25.89, 'As': 30.52, 'O': 32.59}
	Found duplicates of "Uzonite", with these properties :
			Density 3.37, Hardness 1.5, Elements {'As': 65.15, 'S': 34.85}
			Density 3.37, Hardness 1.5, Elements {'As': 65.15, 'S': 34.85}
	Found duplicates of "Utahite", with these properties :
			Density 5.33, Hardness 4.5, Elements {'Zn': 12.72, 'Cu': 20.6, 'Te': 33.09, 'H': 1.44, 'O': 32.16}
			Density 5.33, Hardness 4.5, Elements {'Zn': 12.72, 'Cu': 20.6, 'Te': 33.09, 'H': 1.44, 'O': 32.16}
	Found duplicates of "Uvarovite", with these properties :
			Density 3.59, Hardness 6.75, Elements {'Ca': 24.02, 'Cr': 20.78, 'Si': 16.84, 'O': 38.36}
			Density 3.59, Hardness 6.75, Elements {'Ca': 24.02, 'Cr': 20.78, 'Si': 16.84, 'O': 38.36}
	Found duplicates of "Vajdakite", with these properties :
			Density 3.5, Hardness None, Elements {'Mo': 34.04, 'As': 27.98, 'H': 1.24, 'O': 36.74}
			Density 3.5, Hardness None, Elements {'Mo': 34.04, 'As': 27.98, 'H': 1.24, 'O': 36.74}
	Found duplicates of "Valentinite", with these properties :
			Density 5.69, Hardness 2.75, Elements {'Sb': 83.53, 'O': 16.47}
			Density 5.69, Hardness 2.75, Elements {'Sb': 83.53, 'O': 16.47}
	Found duplicates of "Vanadinite", with these properties :
			Density 6.94, Hardness 3.75, Elements {'V': 10.79, 'Pb': 73.15, 'Cl': 2.5, 'O': 13.56}
			Density 6.94, Hardness 3.75, Elements {'V': 10.79, 'Pb': 73.15, 'Cl': 2.5, 'O': 13.56}
	Found duplicates of "Vanadiocarpholite", with these properties :
			Density None, Hardness None, Elements {'Mn': 15.56, 'Al': 7.64, 'V': 14.43, 'Si': 15.91, 'H': 1.14, 'O': 45.32}
			Density None, Hardness None, Elements {'Mn': 15.56, 'Al': 7.64, 'V': 14.43, 'Si': 15.91, 'H': 1.14, 'O': 45.32}
	Found duplicates of "Vanadiumdravite", with these properties :
			Density 3.32, Hardness 7.5, Elements {'K': 0.35, 'Na': 1.87, 'Mg': 5.04, 'Al': 1.46, 'V': 26.64, 'Cr': 2.34, 'Si': 15.19, 'B': 2.92, 'H': 0.3, 'O': 43.71, 'F': 0.17}
			Density 3.32, Hardness 7.5, Elements {'K': 0.35, 'Na': 1.87, 'Mg': 5.04, 'Al': 1.46, 'V': 26.64, 'Cr': 2.34, 'Si': 15.19, 'B': 2.92, 'H': 0.3, 'O': 43.71, 'F': 0.17}
	Found duplicates of "Vanadoandrosite-Ce", with these properties :
			Density None, Hardness None, Elements {'Sr': 1.66, 'Ca': 4.07, 'La': 3.59, 'Ce': 9.41, 'Sm': 0.52, 'Mg': 0.13, 'Ti': 0.08, 'Mn': 9.27, 'Al': 5.39, 'V': 9.74, 'Fe': 3.17, 'Si': 14.51, 'H': 0.17, 'Nd': 2.48, 'O': 35.81}
			Density None, Hardness None, Elements {'Sr': 1.66, 'Ca': 4.07, 'La': 3.59, 'Ce': 9.41, 'Sm': 0.52, 'Mg': 0.13, 'Ti': 0.08, 'Mn': 9.27, 'Al': 5.39, 'V': 9.74, 'Fe': 3.17, 'Si': 14.51, 'H': 0.17, 'Nd': 2.48, 'O': 35.81}
	Found duplicates of "Vanadomalayaite", with these properties :
			Density 3.6, Hardness 6.0, Elements {'Ca': 20.13, 'V': 25.59, 'Si': 14.11, 'O': 40.18}
			Density 3.6, Hardness 6.0, Elements {'Ca': 20.13, 'V': 25.59, 'Si': 14.11, 'O': 40.18}
	Found duplicates of "Varennesite", with these properties :
			Density 2.31, Hardness 4.0, Elements {'Na': 14.9, 'Mn': 8.9, 'Si': 22.76, 'H': 2.08, 'Cl': 1.44, 'O': 49.91}
			Density 2.31, Hardness 4.0, Elements {'Na': 14.9, 'Mn': 8.9, 'Si': 22.76, 'H': 2.08, 'Cl': 1.44, 'O': 49.91}
	Found duplicates of "Vasilyevite", with these properties :
			Density None, Hardness 3.0, Elements {'Hg': 86.36, 'C': 0.22, 'S': 0.1, 'I': 7.06, 'Br': 2.51, 'Cl': 0.6, 'O': 3.15}
			Density None, Hardness 3.0, Elements {'Hg': 86.36, 'C': 0.22, 'S': 0.1, 'I': 7.06, 'Br': 2.51, 'Cl': 0.6, 'O': 3.15}
	Found duplicates of "Vastmanlandite-Ce", with these properties :
			Density None, Hardness 6.0, Elements {'Ca': 3.37, 'La': 11.8, 'Ce': 20.74, 'Pr': 1.8, 'Sm': 0.41, 'Gd': 0.14, 'Y': 0.16, 'Mg': 3.38, 'Al': 4.63, 'Fe': 2.81, 'Si': 12.68, 'P': 0.03, 'H': 0.18, 'Nd': 5.53, 'O': 31.25, 'F': 1.08}
			Density None, Hardness 6.0, Elements {'Ca': 3.37, 'La': 11.8, 'Ce': 20.74, 'Pr': 1.8, 'Sm': 0.41, 'Gd': 0.14, 'Y': 0.16, 'Mg': 3.38, 'Al': 4.63, 'Fe': 2.81, 'Si': 12.68, 'P': 0.03, 'H': 0.18, 'Nd': 5.53, 'O': 31.25, 'F': 1.08}
	Found duplicates of "Vavrinite", with these properties :
			Density 7.79, Hardness 2.0, Elements {'Fe': 1.25, 'Ni': 23.07, 'Bi': 0.43, 'Sb': 23.8, 'Te': 50.15, 'Pd': 1.3}
			Density 7.79, Hardness 2.0, Elements {'Fe': 1.25, 'Ni': 23.07, 'Bi': 0.43, 'Sb': 23.8, 'Te': 50.15, 'Pd': 1.3}
			Density 7.79, Hardness 2.0, Elements {'Fe': 1.25, 'Ni': 23.07, 'Bi': 0.43, 'Sb': 23.8, 'Te': 50.15, 'Pd': 1.3}
	Found duplicates of "Velikite", with these properties :
			Density 5.45, Hardness 4.0, Elements {'Cu': 22.12, 'Sn': 20.66, 'Hg': 34.91, 'S': 22.32}
			Density 5.45, Hardness 4.0, Elements {'Cu': 22.12, 'Sn': 20.66, 'Hg': 34.91, 'S': 22.32}
	Found duplicates of "Verbeekite", with these properties :
			Density None, Hardness 5.5, Elements {'Pd': 40.26, 'Se': 59.74}
			Density None, Hardness 5.5, Elements {'Pd': 40.26, 'Se': 59.74}
	Found duplicates of "Vergasovaite", with these properties :
			Density None, Hardness 4.5, Elements {'Zn': 1.47, 'Cu': 40.1, 'Mo': 17.3, 'S': 8.67, 'O': 32.45}
			Density None, Hardness 4.5, Elements {'Zn': 1.47, 'Cu': 40.1, 'Mo': 17.3, 'S': 8.67, 'O': 32.45}
	Found duplicates of "Veselovskyite", with these properties :
			Density None, Hardness None, Elements {'Zn': 3.78, 'Co': 0.57, 'Cu': 26.32, 'As': 28.87, 'H': 1.94, 'O': 38.53}
			Density None, Hardness None, Elements {'Zn': 3.78, 'Co': 0.57, 'Cu': 26.32, 'As': 28.87, 'H': 1.94, 'O': 38.53}
	Found duplicates of "Vesignieite", with these properties :
			Density 4.05, Hardness 3.75, Elements {'Ba': 23.2, 'V': 17.21, 'Cu': 32.21, 'H': 0.34, 'O': 27.03}
			Density 4.05, Hardness 3.75, Elements {'Ba': 23.2, 'V': 17.21, 'Cu': 32.21, 'H': 0.34, 'O': 27.03}
	Found duplicates of "Viaeneite", with these properties :
			Density 3.8, Hardness 4.0, Elements {'Fe': 40.33, 'Pb': 7.88, 'S': 48.75, 'O': 3.04}
			Density 3.8, Hardness 4.0, Elements {'Fe': 40.33, 'Pb': 7.88, 'S': 48.75, 'O': 3.04}
	Found duplicates of "Vicanite-Ce", with these properties :
			Density 4.73, Hardness 5.75, Elements {'Na': 0.45, 'Ca': 12.55, 'La': 13.59, 'Ce': 19.2, 'Th': 9.08, 'Fe': 2.19, 'Si': 6.6, 'B': 1.69, 'As': 4.4, 'O': 25.05, 'F': 5.21}
			Density 4.73, Hardness 5.75, Elements {'Na': 0.45, 'Ca': 12.55, 'La': 13.59, 'Ce': 19.2, 'Th': 9.08, 'Fe': 2.19, 'Si': 6.6, 'B': 1.69, 'As': 4.4, 'O': 25.05, 'F': 5.21}
	Found duplicates of "Bermanite", with these properties :
			Density 2.84, Hardness 3.5, Elements {'Mn': 35.76, 'P': 13.44, 'H': 2.19, 'O': 48.61}
			Density 2.84, Hardness 3.5, Elements {'Mn': 35.76, 'P': 13.44, 'H': 2.19, 'O': 48.61}
	Found duplicates of "Villiaumite", with these properties :
			Density 2.79, Hardness 2.5, Elements {'Na': 54.75, 'F': 45.25}
			Density 2.79, Hardness 2.5, Elements {'Na': 54.75, 'F': 45.25}
	Found duplicates of "Vitimite", with these properties :
			Density 2.29, Hardness 1.5, Elements {'Ca': 22.07, 'B': 13.44, 'H': 2.1, 'S': 2.85, 'O': 59.54}
			Density 2.29, Hardness 1.5, Elements {'Ca': 22.07, 'B': 13.44, 'H': 2.1, 'S': 2.85, 'O': 59.54}
	Found duplicates of "Vlodavetsite", with these properties :
			Density 2.35, Hardness None, Elements {'Ca': 18.02, 'Al': 6.07, 'H': 1.81, 'S': 14.42, 'Cl': 7.97, 'O': 43.17, 'F': 8.54}
			Density 2.35, Hardness None, Elements {'Ca': 18.02, 'Al': 6.07, 'H': 1.81, 'S': 14.42, 'Cl': 7.97, 'O': 43.17, 'F': 8.54}
	Found duplicates of "Volkonskoite", with these properties :
			Density 2.25, Hardness 1.75, Elements {'Ca': 0.84, 'Mg': 4.6, 'Al': 2.84, 'Cr': 13.12, 'Fe': 3.52, 'Si': 20.66, 'H': 1.95, 'O': 52.47}
			Density 2.25, Hardness 1.75, Elements {'Ca': 0.84, 'Mg': 4.6, 'Al': 2.84, 'Cr': 13.12, 'Fe': 3.52, 'Si': 20.66, 'H': 1.95, 'O': 52.47}
			Density 2.25, Hardness 1.75, Elements {'Ca': 0.84, 'Mg': 4.6, 'Al': 2.84, 'Cr': 13.12, 'Fe': 3.52, 'Si': 20.66, 'H': 1.95, 'O': 52.47}
	Found duplicates of "Voronkovite", with these properties :
			Density 2.97, Hardness 5.0, Elements {'K': 0.24, 'Na': 11.83, 'Sr': 1.51, 'Ca': 2.22, 'La': 0.8, 'Ce': 1.17, 'Hf': 0.23, 'Zr': 10.51, 'Ti': 0.2, 'Mn': 3.62, 'Nb': 0.62, 'Al': 0.08, 'Fe': 2.75, 'Si': 23.28, 'H': 0.18, 'Nd': 0.6, 'Cl': 0.44, 'O': 39.53, 'F': 0.21}
			Density 2.97, Hardness 5.0, Elements {'K': 0.24, 'Na': 11.83, 'Sr': 1.51, 'Ca': 2.22, 'La': 0.8, 'Ce': 1.17, 'Hf': 0.23, 'Zr': 10.51, 'Ti': 0.2, 'Mn': 3.62, 'Nb': 0.62, 'Al': 0.08, 'Fe': 2.75, 'Si': 23.28, 'H': 0.18, 'Nd': 0.6, 'Cl': 0.44, 'O': 39.53, 'F': 0.21}
	Found duplicates of "Vuoriyarvite-K", with these properties :
			Density 2.95, Hardness 4.5, Elements {'K': 5.98, 'Ba': 2.1, 'Na': 2.11, 'Ti': 0.73, 'Nb': 25.56, 'Si': 17.17, 'H': 1.37, 'O': 44.99}
			Density 2.95, Hardness 4.5, Elements {'K': 5.98, 'Ba': 2.1, 'Na': 2.11, 'Ti': 0.73, 'Nb': 25.56, 'Si': 17.17, 'H': 1.37, 'O': 44.99}
			Density 2.95, Hardness 4.5, Elements {'K': 5.98, 'Ba': 2.1, 'Na': 2.11, 'Ti': 0.73, 'Nb': 25.56, 'Si': 17.17, 'H': 1.37, 'O': 44.99}
			Density 2.95, Hardness 4.5, Elements {'K': 5.98, 'Ba': 2.1, 'Na': 2.11, 'Ti': 0.73, 'Nb': 25.56, 'Si': 17.17, 'H': 1.37, 'O': 44.99}
	Found duplicates of "Vurroite", with these properties :
			Density None, Hardness None, Elements {'Tl': 0.06, 'Sn': 1.61, 'Bi': 28.34, 'As': 8.39, 'Pb': 41.0, 'Se': 0.18, 'S': 18.32, 'Br': 0.24, 'Cl': 1.85}
			Density None, Hardness None, Elements {'Tl': 0.06, 'Sn': 1.61, 'Bi': 28.34, 'As': 8.39, 'Pb': 41.0, 'Se': 0.18, 'S': 18.32, 'Br': 0.24, 'Cl': 1.85}
	Found duplicates of "Walfordite", with these properties :
			Density None, Hardness None, Elements {'Fe': 6.13, 'Te': 72.24, 'O': 21.63}
			Density None, Hardness None, Elements {'Fe': 6.13, 'Te': 72.24, 'O': 21.63}
	Found duplicates of "Walkerite", with these properties :
			Density 2.07, Hardness 3.0, Elements {'K': 0.06, 'Na': 0.1, 'Li': 0.06, 'Ca': 16.36, 'Mg': 0.35, 'Fe': 0.25, 'B': 14.54, 'H': 2.83, 'Cl': 4.88, 'O': 60.58}
			Density 2.07, Hardness 3.0, Elements {'K': 0.06, 'Na': 0.1, 'Li': 0.06, 'Ca': 16.36, 'Mg': 0.35, 'Fe': 0.25, 'B': 14.54, 'H': 2.83, 'Cl': 4.88, 'O': 60.58}
	Found duplicates of "Wallkilldellite-Fe", with these properties :
			Density 3.0, Hardness 2.5, Elements {'Ca': 9.58, 'Fe': 22.24, 'Cu': 1.69, 'Si': 0.56, 'As': 18.4, 'H': 2.94, 'O': 44.6}
			Density 3.0, Hardness 2.5, Elements {'Ca': 9.58, 'Fe': 22.24, 'Cu': 1.69, 'Si': 0.56, 'As': 18.4, 'H': 2.94, 'O': 44.6}
	Found duplicates of "Walpurgite", with these properties :
			Density 5.95, Hardness 3.5, Elements {'U': 16.04, 'Bi': 56.34, 'As': 10.1, 'H': 0.27, 'O': 17.25}
			Density 5.95, Hardness 3.5, Elements {'U': 16.04, 'Bi': 56.34, 'As': 10.1, 'H': 0.27, 'O': 17.25}
	Found duplicates of "Rosslerite", with these properties :
			Density 1.93, Hardness 2.5, Elements {'Mg': 8.37, 'As': 25.8, 'H': 5.21, 'O': 60.62}
			Density 1.93, Hardness 2.5, Elements {'Mg': 8.37, 'As': 25.8, 'H': 5.21, 'O': 60.62}
			Density 1.93, Hardness 2.5, Elements {'Mg': 8.37, 'As': 25.8, 'H': 5.21, 'O': 60.62}
	Found duplicates of "Watatsumiite", with these properties :
			Density None, Hardness 5.75, Elements {'K': 4.06, 'Ba': 0.76, 'Na': 5.28, 'Li': 0.74, 'Mg': 0.97, 'Ti': 1.9, 'Mn': 9.53, 'V': 9.34, 'Fe': 0.25, 'Si': 24.73, 'O': 42.43}
			Density None, Hardness 5.75, Elements {'K': 4.06, 'Ba': 0.76, 'Na': 5.28, 'Li': 0.74, 'Mg': 0.97, 'Ti': 1.9, 'Mn': 9.53, 'V': 9.34, 'Fe': 0.25, 'Si': 24.73, 'O': 42.43}
	Found duplicates of "Waterhouseite", with these properties :
			Density None, Hardness None, Elements {'Mn': 55.0, 'V': 0.28, 'As': 0.72, 'P': 7.71, 'H': 1.08, 'O': 35.2}
			Density None, Hardness None, Elements {'Mn': 55.0, 'V': 0.28, 'As': 0.72, 'P': 7.71, 'H': 1.08, 'O': 35.2}
	Found duplicates of "Aluminite", with these properties :
			Density 1.68, Hardness 1.0, Elements {'Al': 15.68, 'H': 5.27, 'S': 9.32, 'O': 69.73}
			Density 1.68, Hardness 1.0, Elements {'Al': 15.68, 'H': 5.27, 'S': 9.32, 'O': 69.73}
	Found duplicates of "Churchite-Y", with these properties :
			Density 3.265, Hardness 3.0, Elements {'Y': 40.43, 'P': 14.08, 'H': 1.83, 'O': 43.65}
			Density 3.265, Hardness 3.0, Elements {'Y': 40.43, 'P': 14.08, 'H': 1.83, 'O': 43.65}
	Found duplicates of "Scapolite", with these properties :
			Density 2.66, Hardness 6.0, Elements {'Na': 5.24, 'Ca': 9.14, 'Al': 15.38, 'Si': 22.42, 'Cl': 4.04, 'O': 43.78}
			Density 2.66, Hardness 6.0, Elements {'Na': 5.24, 'Ca': 9.14, 'Al': 15.38, 'Si': 22.42, 'Cl': 4.04, 'O': 43.78}
	Found duplicates of "Wesselsite", with these properties :
			Density 3.2, Hardness 4.5, Elements {'Sr': 20.69, 'Cu': 15.0, 'Si': 26.53, 'O': 37.78}
			Density 3.2, Hardness 4.5, Elements {'Sr': 20.69, 'Cu': 15.0, 'Si': 26.53, 'O': 37.78}
	Found duplicates of "Bismutomicrolite", with these properties :
			Density 6.83, Hardness 5.0, Elements {'Ca': 2.72, 'Ta': 52.15, 'Nb': 4.72, 'Bi': 21.26, 'H': 0.17, 'O': 18.99}
			Density 6.83, Hardness 5.0, Elements {'Ca': 2.72, 'Ta': 52.15, 'Nb': 4.72, 'Bi': 21.26, 'H': 0.17, 'O': 18.99}
	Found duplicates of "Bournonite", with these properties :
			Density 5.8, Hardness 3.0, Elements {'Cu': 13.0, 'Sb': 24.91, 'Pb': 42.4, 'S': 19.68}
			Density 5.8, Hardness 3.0, Elements {'Cu': 13.0, 'Sb': 24.91, 'Pb': 42.4, 'S': 19.68}
			Density 5.8, Hardness 3.0, Elements {'Cu': 13.0, 'Sb': 24.91, 'Pb': 42.4, 'S': 19.68}
	Found duplicates of "Cerussite", with these properties :
			Density 6.58, Hardness 3.25, Elements {'Pb': 77.54, 'C': 4.49, 'O': 17.96}
			Density 6.58, Hardness 3.25, Elements {'Pb': 77.54, 'C': 4.49, 'O': 17.96}
	Found duplicates of "Algodonite", with these properties :
			Density 8.55, Hardness 4.0, Elements {'Cu': 83.58, 'As': 16.42}
			Density 8.55, Hardness 4.0, Elements {'Cu': 83.58, 'As': 16.42}
	Found duplicates of "Widgiemoolthalite", with these properties :
			Density 3.13, Hardness 3.5, Elements {'Mg': 6.11, 'Ni': 34.41, 'H': 1.86, 'C': 8.05, 'O': 49.58}
			Density 3.13, Hardness 3.5, Elements {'Mg': 6.11, 'Ni': 34.41, 'H': 1.86, 'C': 8.05, 'O': 49.58}
	Found duplicates of "Wilhelmkleinite", with these properties :
			Density 4.48, Hardness 4.5, Elements {'Zn': 13.37, 'Fe': 22.84, 'As': 30.65, 'H': 0.41, 'O': 32.72}
			Density 4.48, Hardness 4.5, Elements {'Zn': 13.37, 'Fe': 22.84, 'As': 30.65, 'H': 0.41, 'O': 32.72}
	Found duplicates of "Wilhelmramsayite", with these properties :
			Density 2.75, Hardness 2.5, Elements {'K': 0.31, 'Na': 0.12, 'Tl': 0.54, 'Fe': 15.35, 'Cu': 48.87, 'H': 1.05, 'S': 25.42, 'O': 8.33}
			Density 2.75, Hardness 2.5, Elements {'K': 0.31, 'Na': 0.12, 'Tl': 0.54, 'Fe': 15.35, 'Cu': 48.87, 'H': 1.05, 'S': 25.42, 'O': 8.33}
	Found duplicates of "Ellestadite", with these properties :
			Density None, Hardness None, Elements {'Ca': 40.04, 'Si': 10.1, 'P': 5.57, 'H': 0.06, 'S': 1.92, 'Cl': 0.71, 'O': 39.32, 'F': 2.28}
			Density None, Hardness None, Elements {'Ca': 40.04, 'Si': 10.1, 'P': 5.57, 'H': 0.06, 'S': 1.92, 'Cl': 0.71, 'O': 39.32, 'F': 2.28}
	Found duplicates of "Rathite", with these properties :
			Density 5.41, Hardness 3.0, Elements {'Tl': 1.9, 'Ag': 4.02, 'As': 25.81, 'Pb': 44.38, 'S': 23.89}
			Density 5.41, Hardness 3.0, Elements {'Tl': 1.9, 'Ag': 4.02, 'As': 25.81, 'Pb': 44.38, 'S': 23.89}
			Density 5.41, Hardness 3.0, Elements {'Tl': 1.9, 'Ag': 4.02, 'As': 25.81, 'Pb': 44.38, 'S': 23.89}
	Found duplicates of "Wiluite", with these properties :
			Density 3.36, Hardness 6.0, Elements {'Ca': 26.04, 'Mg': 3.99, 'Ti': 0.82, 'Al': 6.46, 'Fe': 1.72, 'Si': 17.28, 'B': 0.96, 'H': 0.07, 'O': 42.67}
			Density 3.36, Hardness 6.0, Elements {'Ca': 26.04, 'Mg': 3.99, 'Ti': 0.82, 'Al': 6.46, 'Fe': 1.72, 'Si': 17.28, 'B': 0.96, 'H': 0.07, 'O': 42.67}
	Found duplicates of "Winchite", with these properties :
			Density 2.96, Hardness 5.5, Elements {'K': 0.47, 'Na': 2.61, 'Ca': 6.15, 'Mg': 12.53, 'Mn': 0.53, 'Al': 1.01, 'Fe': 3.71, 'Si': 26.38, 'H': 0.24, 'O': 46.36}
			Density 2.96, Hardness 5.5, Elements {'K': 0.47, 'Na': 2.61, 'Ca': 6.15, 'Mg': 12.53, 'Mn': 0.53, 'Al': 1.01, 'Fe': 3.71, 'Si': 26.38, 'H': 0.24, 'O': 46.36}
	Found duplicates of "Wohlerite", with these properties :
			Density 3.42, Hardness 5.75, Elements {'Na': 5.82, 'Ca': 19.26, 'Zr': 11.54, 'Nb': 9.4, 'Fe': 1.41, 'Si': 14.21, 'H': 0.26, 'O': 35.22, 'F': 2.88}
			Density 3.42, Hardness 5.75, Elements {'Na': 5.82, 'Ca': 19.26, 'Zr': 11.54, 'Nb': 9.4, 'Fe': 1.41, 'Si': 14.21, 'H': 0.26, 'O': 35.22, 'F': 2.88}
	Found duplicates of "Wolsendorfite", with these properties :
			Density 6.8, Hardness 5.0, Elements {'Ba': 5.19, 'Ca': 0.51, 'U': 59.99, 'H': 0.51, 'Pb': 15.67, 'O': 18.14}
			Density 6.8, Hardness 5.0, Elements {'Ba': 5.19, 'Ca': 0.51, 'U': 59.99, 'H': 0.51, 'Pb': 15.67, 'O': 18.14}
	Found duplicates of "Chalcostibite", with these properties :
			Density 4.87, Hardness 3.5, Elements {'Cu': 25.48, 'Sb': 48.81, 'S': 25.71}
			Density 4.87, Hardness 3.5, Elements {'Cu': 25.48, 'Sb': 48.81, 'S': 25.71}
	Found duplicates of "Wonesite", with these properties :
			Density None, Hardness 2.75, Elements {'K': 0.97, 'Na': 2.28, 'Mg': 13.25, 'Al': 7.36, 'Fe': 5.54, 'Si': 22.27, 'H': 0.37, 'O': 45.6, 'F': 2.35}
			Density None, Hardness 2.75, Elements {'K': 0.97, 'Na': 2.28, 'Mg': 13.25, 'Al': 7.36, 'Fe': 5.54, 'Si': 22.27, 'H': 0.37, 'O': 45.6, 'F': 2.35}
	Found duplicates of "Woodallite", with these properties :
			Density 2.062, Hardness 1.75, Elements {'Mg': 22.86, 'Al': 0.82, 'Cr': 9.46, 'Fe': 4.24, 'H': 3.67, 'C': 0.36, 'Cl': 8.6, 'O': 49.99}
			Density 2.062, Hardness 1.75, Elements {'Mg': 22.86, 'Al': 0.82, 'Cr': 9.46, 'Fe': 4.24, 'H': 3.67, 'C': 0.36, 'Cl': 8.6, 'O': 49.99}
	Found duplicates of "Wooldridgeite", with these properties :
			Density None, Hardness 2.5, Elements {'Na': 6.2, 'Ca': 5.41, 'Cu': 17.15, 'P': 16.72, 'H': 2.72, 'O': 51.81}
			Density None, Hardness 2.5, Elements {'Na': 6.2, 'Ca': 5.41, 'Cu': 17.15, 'P': 16.72, 'H': 2.72, 'O': 51.81}
			Density None, Hardness 2.5, Elements {'Na': 6.2, 'Ca': 5.41, 'Cu': 17.15, 'P': 16.72, 'H': 2.72, 'O': 51.81}
	Found duplicates of "Wulfingite", with these properties :
			Density 3.05, Hardness 3.0, Elements {'Zn': 65.78, 'H': 2.03, 'O': 32.19}
			Density 3.05, Hardness 3.0, Elements {'Zn': 65.78, 'H': 2.03, 'O': 32.19}
	Found duplicates of "Wustite", with these properties :
			Density None, Hardness None, Elements {'Fe': 77.73, 'O': 22.27}
			Density None, Hardness None, Elements {'Fe': 77.73, 'O': 22.27}
	Found duplicates of "Wulfenite", with these properties :
			Density 6.75, Hardness 3.0, Elements {'Mo': 26.13, 'Pb': 56.44, 'O': 17.43}
			Density None, Hardness None, Elements {'Mo': 26.13, 'Pb': 56.44, 'O': 17.43}
	Found duplicates of "Wupatkiite", with these properties :
			Density 1.89, Hardness 1.75, Elements {'Mg': 0.83, 'Al': 6.11, 'Co': 4.0, 'Ni': 0.66, 'H': 5.02, 'S': 14.52, 'O': 68.85}
			Density 1.89, Hardness 1.75, Elements {'Mg': 0.83, 'Al': 6.11, 'Co': 4.0, 'Ni': 0.66, 'H': 5.02, 'S': 14.52, 'O': 68.85}
	Found duplicates of "Wycheproofite", with these properties :
			Density 2.81, Hardness 4.5, Elements {'Na': 6.0, 'Zr': 23.81, 'Al': 7.04, 'P': 16.17, 'H': 1.05, 'O': 45.93}
			Density 2.81, Hardness 4.5, Elements {'Na': 6.0, 'Zr': 23.81, 'Al': 7.04, 'P': 16.17, 'H': 1.05, 'O': 45.93}
	Found duplicates of "Xanthoconite", with these properties :
			Density 5.55, Hardness 2.75, Elements {'Ag': 65.41, 'As': 15.14, 'S': 19.44}
			Density 5.55, Hardness 2.75, Elements {'Ag': 65.41, 'As': 15.14, 'S': 19.44}
	Found duplicates of "Clintonite", with these properties :
			Density 3.05, Hardness 4.5, Elements {'Ca': 9.64, 'Mg': 12.86, 'Al': 22.06, 'Si': 8.78, 'H': 0.48, 'O': 46.17}
			Density 3.05, Hardness 4.5, Elements {'Ca': 9.64, 'Mg': 12.86, 'Al': 22.06, 'Si': 8.78, 'H': 0.48, 'O': 46.17}
			Density 3.05, Hardness 4.5, Elements {'Ca': 9.64, 'Mg': 12.86, 'Al': 22.06, 'Si': 8.78, 'H': 0.48, 'O': 46.17}
			Density 3.05, Hardness 4.5, Elements {'Ca': 9.64, 'Mg': 12.86, 'Al': 22.06, 'Si': 8.78, 'H': 0.48, 'O': 46.17}
			Density 3.05, Hardness 4.5, Elements {'Ca': 9.64, 'Mg': 12.86, 'Al': 22.06, 'Si': 8.78, 'H': 0.48, 'O': 46.17}
	Found duplicates of "Xenophyllite", with these properties :
			Density None, Hardness None, Elements {'Na': 8.74, 'Fe': 37.14, 'P': 17.65, 'O': 36.48}
			Density None, Hardness None, Elements {'Na': 8.74, 'Fe': 37.14, 'P': 17.65, 'O': 36.48}
	Found duplicates of "Xenotime-Yb", with these properties :
			Density None, Hardness None, Elements {'Yb': 64.56, 'P': 11.56, 'O': 23.88}
			Density None, Hardness None, Elements {'Yb': 64.56, 'P': 11.56, 'O': 23.88}
	Found duplicates of "Xocolatlite", with these properties :
			Density 4.1, Hardness 2.5, Elements {'Ca': 12.23, 'Mn': 16.77, 'Te': 38.95, 'H': 0.31, 'O': 31.74}
			Density 4.1, Hardness 2.5, Elements {'Ca': 12.23, 'Mn': 16.77, 'Te': 38.95, 'H': 0.31, 'O': 31.74}
	Found duplicates of "Yakovenchukite-Y", with these properties :
			Density 2.83, Hardness 5.0, Elements {'K': 8.87, 'Na': 3.2, 'Ca': 2.42, 'Ce': 0.11, 'Dy': 0.65, 'Y': 12.17, 'Er': 0.8, 'Tm': 0.13, 'Th': 0.55, 'Yb': 1.38, 'Si': 26.82, 'H': 0.52, 'O': 42.38}
			Density 2.83, Hardness 5.0, Elements {'K': 8.87, 'Na': 3.2, 'Ca': 2.42, 'Ce': 0.11, 'Dy': 0.65, 'Y': 12.17, 'Er': 0.8, 'Tm': 0.13, 'Th': 0.55, 'Yb': 1.38, 'Si': 26.82, 'H': 0.52, 'O': 42.38}
	Found duplicates of "Yazganite", with these properties :
			Density None, Hardness 5.0, Elements {'Na': 3.78, 'Mg': 2.46, 'Mn': 2.92, 'Zn': 0.22, 'Fe': 18.99, 'As': 37.16, 'H': 0.29, 'O': 34.18}
			Density None, Hardness 5.0, Elements {'Na': 3.78, 'Mg': 2.46, 'Mn': 2.92, 'Zn': 0.22, 'Fe': 18.99, 'As': 37.16, 'H': 0.29, 'O': 34.18}
	Found duplicates of "Yixunite", with these properties :
			Density 18.26, Hardness 6.0, Elements {'In': 16.4, 'Pt': 83.6}
			Density 18.26, Hardness 6.0, Elements {'In': 16.4, 'Pt': 83.6}
	Found duplicates of "Calcybeborosilite-Y", with these properties :
			Density None, Hardness None, Elements {'Ca': 9.17, 'RE': 32.95, 'Be': 1.24, 'Fe': 3.83, 'Si': 12.85, 'B': 2.97, 'H': 0.32, 'O': 36.24, 'F': 0.43}
			Density None, Hardness None, Elements {'Ca': 9.17, 'RE': 32.95, 'Be': 1.24, 'Fe': 3.83, 'Si': 12.85, 'B': 2.97, 'H': 0.32, 'O': 36.24, 'F': 0.43}
			Density None, Hardness None, Elements {'Ca': 9.17, 'RE': 32.95, 'Be': 1.24, 'Fe': 3.83, 'Si': 12.85, 'B': 2.97, 'H': 0.32, 'O': 36.24, 'F': 0.43}
	Found duplicates of "Yuanfuliite", with these properties :
			Density 4.02, Hardness 6.25, Elements {'Mg': 13.74, 'Ti': 3.19, 'Al': 3.59, 'Fe': 29.72, 'B': 7.19, 'O': 42.57}
			Density 4.02, Hardness 6.25, Elements {'Mg': 13.74, 'Ti': 3.19, 'Al': 3.59, 'Fe': 29.72, 'B': 7.19, 'O': 42.57}
	Found duplicates of "Yuanjiangite", with these properties :
			Density 11.8, Hardness 3.75, Elements {'Sn': 37.6, 'Au': 62.4}
			Density 11.8, Hardness 3.75, Elements {'Sn': 37.6, 'Au': 62.4}
	Found duplicates of "Yvonite", with these properties :
			Density 3.2, Hardness 3.75, Elements {'Cu': 26.53, 'As': 31.28, 'H': 2.1, 'O': 40.08}
			Density 3.2, Hardness 3.75, Elements {'Cu': 26.53, 'As': 31.28, 'H': 2.1, 'O': 40.08}
	Found duplicates of "Zaccagnaite", with these properties :
			Density None, Hardness None, Elements {'Al': 9.0, 'Zn': 40.49, 'H': 2.88, 'C': 1.91, 'O': 45.73}
			Density None, Hardness None, Elements {'Al': 9.0, 'Zn': 40.49, 'H': 2.88, 'C': 1.91, 'O': 45.73}
	Found duplicates of "Zajacite-Ce", with these properties :
			Density 4.49, Hardness 3.5, Elements {'Na': 7.79, 'Ca': 16.98, 'RE': 36.6, 'F': 38.63}
			Density 4.49, Hardness 3.5, Elements {'Na': 7.79, 'Ca': 16.98, 'RE': 36.6, 'F': 38.63}
	Found duplicates of "Zalesiite", with these properties :
			Density 3.49, Hardness 2.5, Elements {'Ca': 3.19, 'Y': 1.77, 'Cu': 37.94, 'As': 22.37, 'H': 1.3, 'O': 33.43}
			Density 3.49, Hardness 2.5, Elements {'Ca': 3.19, 'Y': 1.77, 'Cu': 37.94, 'As': 22.37, 'H': 1.3, 'O': 33.43}
	Found duplicates of "Zdenekite", with these properties :
			Density 4.1, Hardness 1.75, Elements {'Na': 1.87, 'Cu': 25.85, 'As': 24.38, 'H': 0.82, 'Pb': 16.86, 'Cl': 2.88, 'O': 27.34}
			Density 4.1, Hardness 1.75, Elements {'Na': 1.87, 'Cu': 25.85, 'As': 24.38, 'H': 0.82, 'Pb': 16.86, 'Cl': 2.88, 'O': 27.34}
	Found duplicates of "Zeravshanite", with these properties :
			Density 3.09, Hardness 6.0, Elements {'Cs': 23.84, 'K': 0.04, 'Na': 2.37, 'Zr': 11.75, 'Ti': 0.43, 'Fe': 0.11, 'Si': 23.86, 'Sn': 0.22, 'H': 0.38, 'O': 37.0}
			Density 3.09, Hardness 6.0, Elements {'Cs': 23.84, 'K': 0.04, 'Na': 2.37, 'Zr': 11.75, 'Ti': 0.43, 'Fe': 0.11, 'Si': 23.86, 'Sn': 0.22, 'H': 0.38, 'O': 37.0}
	Found duplicates of "Zhangpeishanite", with these properties :
			Density None, Hardness 2.5, Elements {'Ba': 71.21, 'Cl': 18.94, 'F': 9.85}
			Density None, Hardness 2.5, Elements {'Ba': 71.21, 'Cl': 18.94, 'F': 9.85}
	Found duplicates of "Zincmelanterite", with these properties :
			Density 2.02, Hardness 2.0, Elements {'Zn': 13.72, 'Fe': 1.95, 'Cu': 6.66, 'H': 4.93, 'S': 11.21, 'O': 61.52}
			Density 2.02, Hardness 2.0, Elements {'Zn': 13.72, 'Fe': 1.95, 'Cu': 6.66, 'H': 4.93, 'S': 11.21, 'O': 61.52}
	Found duplicates of "Zincalstibite", with these properties :
			Density None, Hardness None, Elements {'Al': 5.58, 'Zn': 27.04, 'Sb': 25.18, 'H': 2.5, 'O': 39.7}
			Density None, Hardness None, Elements {'Al': 5.58, 'Zn': 27.04, 'Sb': 25.18, 'H': 2.5, 'O': 39.7}
	Found duplicates of "Zincgartrellite", with these properties :
			Density None, Hardness 4.5, Elements {'Zn': 9.13, 'Fe': 5.2, 'Cu': 4.93, 'As': 23.25, 'H': 0.5, 'Pb': 32.15, 'O': 24.83}
			Density None, Hardness 4.5, Elements {'Zn': 9.13, 'Fe': 5.2, 'Cu': 4.93, 'As': 23.25, 'H': 0.5, 'Pb': 32.15, 'O': 24.83}
	Found duplicates of "Zinkenite", with these properties :
			Density 5.23, Hardness 3.25, Elements {'Sb': 45.47, 'Pb': 31.66, 'S': 22.87}
			Density 5.23, Hardness 3.25, Elements {'Sb': 45.47, 'Pb': 31.66, 'S': 22.87}
	Found duplicates of "Zincohogbomite-2N2S", with these properties :
			Density 4.36, Hardness 7.0, Elements {'Ti': 3.43, 'Al': 32.18, 'Zn': 15.4, 'Fe': 10.82, 'O': 38.17}
			Density 4.36, Hardness 7.0, Elements {'Ti': 3.43, 'Al': 32.18, 'Zn': 15.4, 'Fe': 10.82, 'O': 38.17}
			Density 4.36, Hardness 7.0, Elements {'Ti': 3.43, 'Al': 32.18, 'Zn': 15.4, 'Fe': 10.82, 'O': 38.17}
			Density 4.36, Hardness 7.0, Elements {'Ti': 3.43, 'Al': 32.18, 'Zn': 15.4, 'Fe': 10.82, 'O': 38.17}
	Found duplicates of "Zincolibethenite", with these properties :
			Density None, Hardness 3.5, Elements {'Zn': 27.73, 'Cu': 25.84, 'P': 12.81, 'H': 0.42, 'O': 33.2}
			Density None, Hardness 3.5, Elements {'Zn': 27.73, 'Cu': 25.84, 'P': 12.81, 'H': 0.42, 'O': 33.2}
	Found duplicates of "Zincolivenite", with these properties :
			Density 4.34, Hardness None, Elements {'Zn': 22.95, 'Cu': 22.31, 'As': 26.3, 'H': 0.35, 'O': 28.08}
			Density 4.34, Hardness None, Elements {'Zn': 22.95, 'Cu': 22.31, 'As': 26.3, 'H': 0.35, 'O': 28.08}
	Found duplicates of "Zincosite", with these properties :
			Density 4.33, Hardness None, Elements {'Zn': 40.5, 'S': 19.86, 'O': 39.64}
			Density 4.33, Hardness None, Elements {'Zn': 40.5, 'S': 19.86, 'O': 39.64}
			Density 4.33, Hardness None, Elements {'Zn': 40.5, 'S': 19.86, 'O': 39.64}
	Found duplicates of "Zincospiroffite", with these properties :
			Density None, Hardness 2.5, Elements {'Zn': 20.38, 'Te': 59.67, 'O': 19.95}
			Density None, Hardness 2.5, Elements {'Zn': 20.38, 'Te': 59.67, 'O': 19.95}
	Found duplicates of "Zincostaurolite", with these properties :
			Density None, Hardness 7.25, Elements {'Li': 0.21, 'Mg': 0.28, 'Ti': 0.06, 'Al': 29.01, 'Zn': 9.61, 'Fe': 1.11, 'Si': 13.48, 'H': 0.22, 'O': 46.03}
			Density None, Hardness 7.25, Elements {'Li': 0.21, 'Mg': 0.28, 'Ti': 0.06, 'Al': 29.01, 'Zn': 9.61, 'Fe': 1.11, 'Si': 13.48, 'H': 0.22, 'O': 46.03}
	Found duplicates of "Zincowoodwardite-1T", with these properties :
			Density 2.71, Hardness 1.0, Elements {'Al': 7.33, 'Zn': 35.52, 'B': 2.67, 'H': 3.25, 'S': 4.09, 'O': 47.14}
			Density 2.71, Hardness 1.0, Elements {'Al': 7.33, 'Zn': 35.52, 'B': 2.67, 'H': 3.25, 'S': 4.09, 'O': 47.14}
	Found duplicates of "Zincowoodwardite-3R", with these properties :
			Density 2.66, Hardness 1.0, Elements {'Al': 8.17, 'Zn': 29.99, 'B': 3.27, 'H': 2.94, 'S': 5.88, 'O': 49.75}
			Density 2.66, Hardness 1.0, Elements {'Al': 8.17, 'Zn': 29.99, 'B': 3.27, 'H': 2.94, 'S': 5.88, 'O': 49.75}
	Found duplicates of "Cinnabar", with these properties :
			Density 8.1, Hardness 2.25, Elements {'Hg': 86.22, 'S': 13.78}
			Density 8.1, Hardness 2.25, Elements {'Hg': 86.22, 'S': 13.78}
			Density 8.1, Hardness 2.25, Elements {'Hg': 86.22, 'S': 13.78}
	Found duplicates of "Zirkelite", with these properties :
			Density 4.7, Hardness 5.5, Elements {'Ca': 6.28, 'Ce': 3.66, 'Th': 6.06, 'Zr': 23.83, 'Ti': 18.76, 'Nb': 12.14, 'O': 29.26}
			Density 4.7, Hardness 5.5, Elements {'Ca': 6.28, 'Ce': 3.66, 'Th': 6.06, 'Zr': 23.83, 'Ti': 18.76, 'Nb': 12.14, 'O': 29.26}
	Found duplicates of "Zirsilite-Ce", with these properties :
			Density 3.15, Hardness 5.0, Elements {'Na': 6.77, 'Ca': 7.26, 'Ce': 9.52, 'Zr': 8.27, 'Mn': 4.98, 'Nb': 2.81, 'Si': 21.21, 'H': 0.15, 'C': 0.36, 'O': 38.66}
			Density 3.15, Hardness 5.0, Elements {'Na': 6.77, 'Ca': 7.26, 'Ce': 9.52, 'Zr': 8.27, 'Mn': 4.98, 'Nb': 2.81, 'Si': 21.21, 'H': 0.15, 'C': 0.36, 'O': 38.66}
	Found duplicates of "Zlatogorite", with these properties :
			Density 8.21, Hardness 4.5, Elements {'Cu': 17.37, 'Ni': 16.05, 'Sb': 66.58}
			Density 8.21, Hardness 4.5, Elements {'Cu': 17.37, 'Ni': 16.05, 'Sb': 66.58}
	Found duplicates of "Zoltaiite", with these properties :
			Density None, Hardness 6.5, Elements {'Ba': 10.76, 'Ti': 4.68, 'V': 44.66, 'Cr': 1.32, 'Fe': 2.04, 'Si': 4.32, 'O': 32.23}
			Density None, Hardness 6.5, Elements {'Ba': 10.76, 'Ti': 4.68, 'V': 44.66, 'Cr': 1.32, 'Fe': 2.04, 'Si': 4.32, 'O': 32.23}
	Found duplicates of "Zugshunstite-Ce", with these properties :
			Density None, Hardness None, Elements {'La': 2.08, 'Ce': 10.5, 'Pr': 2.11, 'Al': 3.64, 'Fe': 0.84, 'H': 3.62, 'C': 3.6, 'S': 9.61, 'Nd': 6.48, 'O': 57.53}
			Density None, Hardness None, Elements {'La': 2.08, 'Ce': 10.5, 'Pr': 2.11, 'Al': 3.64, 'Fe': 0.84, 'H': 3.62, 'C': 3.6, 'S': 9.61, 'Nd': 6.48, 'O': 57.53}
	Found duplicates of "Abenakiite-Ce", with these properties :
			Density 3.21, Hardness 4.0, Elements {'Na': 20.41, 'RE': 29.51, 'Si': 5.75, 'P': 6.35, 'C': 2.46, 'S': 1.1, 'O': 34.42}
			Density 3.21, Hardness 4.0, Elements {'Na': 20.41, 'RE': 29.51, 'Si': 5.75, 'P': 6.35, 'C': 2.46, 'S': 1.1, 'O': 34.42}
	Found duplicates of "Abramovite", with these properties :
			Density None, Hardness None, Elements {'In': 11.41, 'Sn': 12.13, 'Bi': 17.44, 'Pb': 37.3, 'Se': 0.96, 'S': 20.75}
			Density None, Hardness None, Elements {'In': 11.41, 'Sn': 12.13, 'Bi': 17.44, 'Pb': 37.3, 'Se': 0.96, 'S': 20.75}
	Found duplicates of "Phillipsite-Na", with these properties :
			Density 2.2, Hardness 4.5, Elements {'K': 3.61, 'Na': 3.18, 'Ca': 3.08, 'Al': 11.63, 'Si': 22.47, 'H': 1.86, 'O': 54.16}
			Density 2.2, Hardness 4.5, Elements {'K': 3.61, 'Na': 3.18, 'Ca': 3.08, 'Al': 11.63, 'Si': 22.47, 'H': 1.86, 'O': 54.16}
			Density 2.2, Hardness 4.5, Elements {'K': 3.61, 'Na': 3.18, 'Ca': 3.08, 'Al': 11.63, 'Si': 22.47, 'H': 1.86, 'O': 54.16}
	Found duplicates of "Britholite-Y", with these properties :
			Density 4.25, Hardness 5.0, Elements {'Ca': 12.47, 'Y': 41.49, 'Si': 9.83, 'P': 3.61, 'H': 0.12, 'O': 31.74, 'F': 0.74}
			Density 4.25, Hardness 5.0, Elements {'Ca': 12.47, 'Y': 41.49, 'Si': 9.83, 'P': 3.61, 'H': 0.12, 'O': 31.74, 'F': 0.74}
	Found duplicates of "Chabazite-Ca", with these properties :
			Density 2.09, Hardness 4.0, Elements {'K': 0.75, 'Na': 0.07, 'Sr': 0.25, 'Ca': 7.17, 'Mg': 0.05, 'Al': 10.23, 'Si': 21.7, 'H': 2.55, 'O': 57.22}
			Density 2.09, Hardness 4.0, Elements {'K': 0.75, 'Na': 0.07, 'Sr': 0.25, 'Ca': 7.17, 'Mg': 0.05, 'Al': 10.23, 'Si': 21.7, 'H': 2.55, 'O': 57.22}
			Density 2.09, Hardness 4.0, Elements {'K': 0.75, 'Na': 0.07, 'Sr': 0.25, 'Ca': 7.17, 'Mg': 0.05, 'Al': 10.23, 'Si': 21.7, 'H': 2.55, 'O': 57.22}
			Density 2.09, Hardness 4.0, Elements {'K': 0.75, 'Na': 0.07, 'Sr': 0.25, 'Ca': 7.17, 'Mg': 0.05, 'Al': 10.23, 'Si': 21.7, 'H': 2.55, 'O': 57.22}
	Found duplicates of "Acetamide", with these properties :
			Density 1.17, Hardness 1.25, Elements {'H': 8.53, 'C': 40.67, 'N': 23.71, 'O': 27.09}
			Density 1.17, Hardness 1.25, Elements {'H': 8.53, 'C': 40.67, 'N': 23.71, 'O': 27.09}
	Found duplicates of "Aegirine", with these properties :
			Density 3.52, Hardness 6.25, Elements {'Na': 9.95, 'Fe': 24.18, 'Si': 24.32, 'O': 41.56}
			Density 3.52, Hardness 6.25, Elements {'Na': 9.95, 'Fe': 24.18, 'Si': 24.32, 'O': 41.56}
	Found duplicates of "Adamite", with these properties :
			Density 4.4, Hardness 3.5, Elements {'Zn': 45.61, 'As': 26.13, 'H': 0.35, 'O': 27.9}
			Density 4.4, Hardness 3.5, Elements {'Zn': 45.61, 'As': 26.13, 'H': 0.35, 'O': 27.9}
	Found duplicates of "Adamsite-Y", with these properties :
			Density None, Hardness 3.0, Elements {'Na': 6.68, 'Gd': 1.83, 'Dy': 4.72, 'Y': 18.08, 'Er': 2.43, 'H': 3.51, 'C': 6.98, 'O': 55.77}
			Density None, Hardness 3.0, Elements {'Na': 6.68, 'Gd': 1.83, 'Dy': 4.72, 'Y': 18.08, 'Er': 2.43, 'H': 3.51, 'C': 6.98, 'O': 55.77}
	Found duplicates of "Aenigmatite", with these properties :
			Density 3.79, Hardness 5.5, Elements {'Na': 4.99, 'Ca': 0.77, 'Mg': 0.65, 'Zr': 0.05, 'Ti': 5.85, 'Mn': 1.28, 'Al': 1.01, 'Zn': 0.04, 'Fe': 29.95, 'Si': 18.15, 'O': 37.26}
			Density 3.79, Hardness 5.5, Elements {'Na': 4.99, 'Ca': 0.77, 'Mg': 0.65, 'Zr': 0.05, 'Ti': 5.85, 'Mn': 1.28, 'Al': 1.01, 'Zn': 0.04, 'Fe': 29.95, 'Si': 18.15, 'O': 37.26}
	Found duplicates of "Aeschynite-Y", with these properties :
			Density 4.99, Hardness 5.5, Elements {'Ca': 4.37, 'Y': 19.4, 'Ti': 30.47, 'Nb': 8.45, 'Fe': 2.03, 'H': 0.37, 'O': 34.91}
			Density 4.99, Hardness 5.5, Elements {'Ca': 4.37, 'Y': 19.4, 'Ti': 30.47, 'Nb': 8.45, 'Fe': 2.03, 'H': 0.37, 'O': 34.91}
	Found duplicates of "Agardite-Ce", with these properties :
			Density 3.72, Hardness 3.0, Elements {'Ca': 0.86, 'Eu': 0.28, 'La': 1.95, 'Ce': 4.32, 'Sm': 0.42, 'Gd': 0.44, 'Dy': 0.15, 'Y': 0.75, 'Fe': 0.26, 'Cu': 34.3, 'Si': 0.45, 'As': 20.15, 'H': 1.24, 'S': 0.15, 'Nd': 2.02, 'O': 32.27}
			Density 3.72, Hardness 3.0, Elements {'Ca': 0.86, 'Eu': 0.28, 'La': 1.95, 'Ce': 4.32, 'Sm': 0.42, 'Gd': 0.44, 'Dy': 0.15, 'Y': 0.75, 'Fe': 0.26, 'Cu': 34.3, 'Si': 0.45, 'As': 20.15, 'H': 1.24, 'S': 0.15, 'Nd': 2.02, 'O': 32.27}
	Found duplicates of "Agardite-Y", with these properties :
			Density 3.69, Hardness 3.5, Elements {'Ca': 0.97, 'Y': 6.47, 'Cu': 36.99, 'As': 21.8, 'H': 1.17, 'O': 32.59}
			Density 3.69, Hardness 3.5, Elements {'Ca': 0.97, 'Y': 6.47, 'Cu': 36.99, 'As': 21.8, 'H': 1.17, 'O': 32.59}
	Found duplicates of "Ahlfeldite", with these properties :
			Density 3.37, Hardness 2.25, Elements {'Co': 6.64, 'Ni': 19.85, 'H': 1.82, 'Se': 35.61, 'O': 36.08}
			Density 3.37, Hardness 2.25, Elements {'Co': 6.64, 'Ni': 19.85, 'H': 1.82, 'Se': 35.61, 'O': 36.08}
	Found duplicates of "Akimotoite", with these properties :
			Density None, Hardness None, Elements {'Mg': 16.84, 'Fe': 12.89, 'Si': 25.94, 'O': 44.33}
			Density None, Hardness None, Elements {'Mg': 16.84, 'Fe': 12.89, 'Si': 25.94, 'O': 44.33}
	Found duplicates of "Alarsite", with these properties :
			Density 3.33, Hardness 3.0, Elements {'Al': 16.26, 'As': 45.16, 'O': 38.58}
			Density 3.33, Hardness 3.0, Elements {'Al': 16.26, 'As': 45.16, 'O': 38.58}
	Found duplicates of "Albite", with these properties :
			Density 2.62, Hardness 7.0, Elements {'Na': 8.3, 'Ca': 0.76, 'Al': 10.77, 'Si': 31.5, 'O': 48.66}
			Density 2.62, Hardness 7.0, Elements {'Na': 8.3, 'Ca': 0.76, 'Al': 10.77, 'Si': 31.5, 'O': 48.66}
	Found duplicates of "Chrysoberyl", with these properties :
			Density 3.67, Hardness 8.5, Elements {'Be': 7.1, 'Al': 42.5, 'O': 50.4}
			Density 3.67, Hardness 8.5, Elements {'Be': 7.1, 'Al': 42.5, 'O': 50.4}
	Found duplicates of "Allabogdanite", with these properties :
			Density None, Hardness 5.5, Elements {'Fe': 57.69, 'Co': 1.22, 'Ni': 20.61, 'P': 20.48}
			Density None, Hardness 5.5, Elements {'Fe': 57.69, 'Co': 1.22, 'Ni': 20.61, 'P': 20.48}
	Found duplicates of "Allactite", with these properties :
			Density None, Hardness None, Elements {'Mn': 48.16, 'As': 18.77, 'H': 1.01, 'O': 32.06}
			Density None, Hardness None, Elements {'Mn': 48.16, 'As': 18.77, 'H': 1.01, 'O': 32.06}
	Found duplicates of "Allanite-La", with these properties :
			Density 3.93, Hardness 6.0, Elements {'Ca': 9.19, 'La': 7.17, 'Ce': 5.18, 'Pr': 2.68, 'Y': 0.02, 'Th': 0.25, 'Mg': 0.19, 'Ti': 0.06, 'Al': 9.29, 'Fe': 10.5, 'Si': 15.0, 'H': 0.18, 'Nd': 3.0, 'O': 37.29}
			Density 3.93, Hardness 6.0, Elements {'Ca': 9.19, 'La': 7.17, 'Ce': 5.18, 'Pr': 2.68, 'Y': 0.02, 'Th': 0.25, 'Mg': 0.19, 'Ti': 0.06, 'Al': 9.29, 'Fe': 10.5, 'Si': 15.0, 'H': 0.18, 'Nd': 3.0, 'O': 37.29}
	Found duplicates of "Allanpringite", with these properties :
			Density 2.54, Hardness 3.0, Elements {'Al': 0.16, 'Fe': 33.41, 'P': 12.44, 'H': 2.62, 'O': 51.36}
			Density 2.54, Hardness 3.0, Elements {'Al': 0.16, 'Fe': 33.41, 'P': 12.44, 'H': 2.62, 'O': 51.36}
	Found duplicates of "Allendeite", with these properties :
			Density None, Hardness None, Elements {'Ca': 1.92, 'Y': 0.55, 'Hf': 1.1, 'Zr': 41.5, 'Sc': 20.87, 'Ti': 3.25, 'Al': 0.37, 'V': 0.24, 'Fe': 0.6, 'O': 29.61}
			Density None, Hardness None, Elements {'Ca': 1.92, 'Y': 0.55, 'Hf': 1.1, 'Zr': 41.5, 'Sc': 20.87, 'Ti': 3.25, 'Al': 0.37, 'V': 0.24, 'Fe': 0.6, 'O': 29.61}
	Found duplicates of "Allochalcoselite", with these properties :
			Density None, Hardness 3.5, Elements {'Cu': 35.47, 'Pb': 20.91, 'Se': 14.82, 'Cl': 16.75, 'O': 12.05}
			Density None, Hardness 3.5, Elements {'Cu': 35.47, 'Pb': 20.91, 'Se': 14.82, 'Cl': 16.75, 'O': 12.05}
	Found duplicates of "Alloriite", with these properties :
			Density 2.35, Hardness 5.0, Elements {'K': 5.57, 'Na': 10.1, 'Ca': 4.47, 'Al': 14.06, 'Si': 16.26, 'H': 0.24, 'C': 0.19, 'S': 3.59, 'Cl': 0.37, 'O': 45.15}
			Density 2.35, Hardness 5.0, Elements {'K': 5.57, 'Na': 10.1, 'Ca': 4.47, 'Al': 14.06, 'Si': 16.26, 'H': 0.24, 'C': 0.19, 'S': 3.59, 'Cl': 0.37, 'O': 45.15}
	Found duplicates of "Almandine", with these properties :
			Density 4.19, Hardness 7.5, Elements {'Al': 10.84, 'Fe': 33.66, 'Si': 16.93, 'O': 38.57}
			Density 4.19, Hardness 7.5, Elements {'Al': 10.84, 'Fe': 33.66, 'Si': 16.93, 'O': 38.57}
	Found duplicates of "Almarudite", with these properties :
			Density None, Hardness None, Elements {'K': 3.36, 'Na': 0.48, 'Ca': 0.08, 'Mg': 0.92, 'Mn': 5.66, 'Be': 1.86, 'Al': 2.16, 'Zn': 0.2, 'Fe': 3.46, 'Si': 33.83, 'O': 47.98}
			Density None, Hardness None, Elements {'K': 3.36, 'Na': 0.48, 'Ca': 0.08, 'Mg': 0.92, 'Mn': 5.66, 'Be': 1.86, 'Al': 2.16, 'Zn': 0.2, 'Fe': 3.46, 'Si': 33.83, 'O': 47.98}
	Found duplicates of "Carnallite", with these properties :
			Density 1.6, Hardness 2.5, Elements {'K': 14.07, 'Mg': 8.75, 'H': 4.35, 'Cl': 38.28, 'O': 34.55}
			Density 1.6, Hardness 2.5, Elements {'K': 14.07, 'Mg': 8.75, 'H': 4.35, 'Cl': 38.28, 'O': 34.55}
	Found duplicates of "Alpersite", with these properties :
			Density None, Hardness 2.5, Elements {'Mg': 5.37, 'Mn': 0.42, 'Zn': 0.5, 'Fe': 0.21, 'Cu': 8.95, 'H': 5.37, 'S': 12.2, 'O': 66.98}
			Density None, Hardness 2.5, Elements {'Mg': 5.37, 'Mn': 0.42, 'Zn': 0.5, 'Fe': 0.21, 'Cu': 8.95, 'H': 5.37, 'S': 12.2, 'O': 66.98}
	Found duplicates of "Alsakharovite-Zn", with these properties :
			Density 2.9, Hardness 5.0, Elements {'K': 1.99, 'Ba': 3.21, 'Na': 1.5, 'Sr': 3.74, 'Ca': 1.03, 'Ti': 11.19, 'Nb': 8.69, 'Zn': 4.01, 'Fe': 0.18, 'Si': 18.11, 'H': 1.23, 'O': 45.13}
			Density 2.9, Hardness 5.0, Elements {'K': 1.99, 'Ba': 3.21, 'Na': 1.5, 'Sr': 3.74, 'Ca': 1.03, 'Ti': 11.19, 'Nb': 8.69, 'Zn': 4.01, 'Fe': 0.18, 'Si': 18.11, 'H': 1.23, 'O': 45.13}
	Found duplicates of "Altisite", with these properties :
			Density 2.65, Hardness 6.0, Elements {'K': 19.54, 'Na': 5.75, 'Ti': 7.98, 'Al': 4.5, 'Si': 18.72, 'Cl': 8.86, 'O': 34.66}
			Density 2.65, Hardness 6.0, Elements {'K': 19.54, 'Na': 5.75, 'Ti': 7.98, 'Al': 4.5, 'Si': 18.72, 'Cl': 8.86, 'O': 34.66}
	Found duplicates of "Alum-Na", with these properties :
			Density 1.67, Hardness 3.0, Elements {'Na': 5.02, 'Al': 5.89, 'H': 5.28, 'S': 13.99, 'O': 69.82}
			Density 1.67, Hardness 3.0, Elements {'Na': 5.02, 'Al': 5.89, 'H': 5.28, 'S': 13.99, 'O': 69.82}
			Density 1.67, Hardness 3.0, Elements {'Na': 5.02, 'Al': 5.89, 'H': 5.28, 'S': 13.99, 'O': 69.82}
	Found duplicates of "Alunite", with these properties :
			Density 2.74, Hardness 3.75, Elements {'K': 9.44, 'Al': 19.54, 'H': 1.46, 'S': 15.48, 'O': 54.08}
			Density 2.74, Hardness 3.75, Elements {'K': 9.44, 'Al': 19.54, 'H': 1.46, 'S': 15.48, 'O': 54.08}
	Found duplicates of "Aluminum", with these properties :
			Density 2.7, Hardness 1.5, Elements {'Al': 100.0}
			Density 2.7, Hardness 1.5, Elements {'Al': 100.0}
	Found duplicates of "Aluminomagnesiohulsite", with these properties :
			Density None, Hardness None, Elements {'Mg': 21.41, 'Ti': 0.49, 'Mn': 0.28, 'Al': 8.71, 'Fe': 12.87, 'Sn': 9.73, 'B': 5.54, 'O': 40.97}
			Density None, Hardness None, Elements {'Mg': 21.41, 'Ti': 0.49, 'Mn': 0.28, 'Al': 8.71, 'Fe': 12.87, 'Sn': 9.73, 'B': 5.54, 'O': 40.97}
			Density None, Hardness None, Elements {'Mg': 21.41, 'Ti': 0.49, 'Mn': 0.28, 'Al': 8.71, 'Fe': 12.87, 'Sn': 9.73, 'B': 5.54, 'O': 40.97}
	Found duplicates of "Alumino-magnesiotaramite", with these properties :
			Density None, Hardness None, Elements {'Na': 4.75, 'Ca': 5.57, 'Mg': 6.7, 'Ti': 0.17, 'Al': 9.64, 'Fe': 9.17, 'Si': 19.65, 'H': 0.23, 'O': 44.11}
			Density None, Hardness None, Elements {'Na': 4.75, 'Ca': 5.57, 'Mg': 6.7, 'Ti': 0.17, 'Al': 9.64, 'Fe': 9.17, 'Si': 19.65, 'H': 0.23, 'O': 44.11}
			Density None, Hardness None, Elements {'Na': 4.75, 'Ca': 5.57, 'Mg': 6.7, 'Ti': 0.17, 'Al': 9.64, 'Fe': 9.17, 'Si': 19.65, 'H': 0.23, 'O': 44.11}
	Found duplicates of "Aluminotaramite", with these properties :
			Density None, Hardness None, Elements {'K': 0.04, 'Na': 4.4, 'Ca': 4.8, 'Mg': 4.41, 'Ti': 0.38, 'Mn': 0.06, 'Al': 8.73, 'Zn': 0.07, 'Fe': 14.26, 'Si': 19.59, 'H': 0.21, 'O': 42.75, 'F': 0.3}
			Density None, Hardness None, Elements {'K': 0.04, 'Na': 4.4, 'Ca': 4.8, 'Mg': 4.41, 'Ti': 0.38, 'Mn': 0.06, 'Al': 8.73, 'Zn': 0.07, 'Fe': 14.26, 'Si': 19.59, 'H': 0.21, 'O': 42.75, 'F': 0.3}
	Found duplicates of "Alumoklyuchevskite", with these properties :
			Density 3.1, Hardness 2.0, Elements {'K': 15.62, 'Al': 3.59, 'Cu': 25.38, 'S': 17.08, 'O': 38.34}
			Density 3.1, Hardness 2.0, Elements {'K': 15.62, 'Al': 3.59, 'Cu': 25.38, 'S': 17.08, 'O': 38.34}
	Found duplicates of "Analcime", with these properties :
			Density 2.3, Hardness 5.0, Elements {'Na': 10.44, 'Al': 12.26, 'Si': 25.51, 'H': 0.92, 'O': 50.87}
			Density 2.3, Hardness 5.0, Elements {'Na': 10.44, 'Al': 12.26, 'Si': 25.51, 'H': 0.92, 'O': 50.87}
			Density 2.3, Hardness 5.0, Elements {'Na': 10.44, 'Al': 12.26, 'Si': 25.51, 'H': 0.92, 'O': 50.87}
			Density 2.3, Hardness 5.0, Elements {'Na': 10.44, 'Al': 12.26, 'Si': 25.51, 'H': 0.92, 'O': 50.87}
	Found duplicates of "Ancylite-La", with these properties :
			Density 3.88, Hardness 4.25, Elements {'Sr': 22.95, 'La': 27.28, 'Ce': 9.17, 'H': 0.79, 'C': 6.29, 'O': 33.52}
			Density 3.88, Hardness 4.25, Elements {'Sr': 22.95, 'La': 27.28, 'Ce': 9.17, 'H': 0.79, 'C': 6.29, 'O': 33.52}
	Found duplicates of "Andalusite", with these properties :
			Density 3.15, Hardness 6.75, Elements {'Al': 33.3, 'Si': 17.33, 'O': 49.37}
			Density 3.15, Hardness 6.75, Elements {'Al': 33.3, 'Si': 17.33, 'O': 49.37}
	Found duplicates of "Rockbridgeite", with these properties :
			Density 3.39, Hardness 4.5, Elements {'Mn': 2.12, 'Fe': 40.88, 'P': 14.32, 'H': 0.78, 'O': 41.91}
			Density 3.39, Hardness 4.5, Elements {'Mn': 2.12, 'Fe': 40.88, 'P': 14.32, 'H': 0.78, 'O': 41.91}
	Found duplicates of "Andreyivanovite", with these properties :
			Density None, Hardness None, Elements {'Ti': 2.79, 'V': 4.0, 'Cr': 21.99, 'Fe': 46.27, 'Co': 0.08, 'Ni': 2.54, 'P': 22.32}
			Density None, Hardness None, Elements {'Ti': 2.79, 'V': 4.0, 'Cr': 21.99, 'Fe': 46.27, 'Co': 0.08, 'Ni': 2.54, 'P': 22.32}
	Found duplicates of "Andyrobertsite", with these properties :
			Density 4.011, Hardness 3.0, Elements {'K': 3.25, 'Cd': 9.35, 'Cu': 26.44, 'As': 31.17, 'H': 0.5, 'O': 29.29}
			Density 4.011, Hardness 3.0, Elements {'K': 3.25, 'Cd': 9.35, 'Cu': 26.44, 'As': 31.17, 'H': 0.5, 'O': 29.29}
	Found duplicates of "Angelaite", with these properties :
			Density None, Hardness None, Elements {'Cu': 16.31, 'Ag': 13.84, 'Bi': 26.81, 'Pb': 26.58, 'S': 16.46}
			Density None, Hardness None, Elements {'Cu': 16.31, 'Ag': 13.84, 'Bi': 26.81, 'Pb': 26.58, 'S': 16.46}
	Found duplicates of "Ankinovichite", with these properties :
			Density 2.48, Hardness 2.75, Elements {'Al': 17.91, 'V': 15.89, 'Zn': 1.84, 'Fe': 0.09, 'Cu': 0.21, 'Si': 0.28, 'Ni': 6.62, 'H': 2.92, 'O': 54.23}
			Density 2.48, Hardness 2.75, Elements {'Al': 17.91, 'V': 15.89, 'Zn': 1.84, 'Fe': 0.09, 'Cu': 0.21, 'Si': 0.28, 'Ni': 6.62, 'H': 2.92, 'O': 54.23}
	Found duplicates of "Anorthoclase", with these properties :
			Density 2.58, Hardness 6.0, Elements {'K': 3.67, 'Na': 6.48, 'Al': 10.13, 'Si': 31.65, 'O': 48.07}
			Density 2.58, Hardness 6.0, Elements {'K': 3.67, 'Na': 6.48, 'Al': 10.13, 'Si': 31.65, 'O': 48.07}
	Found duplicates of "Edingtonite", with these properties :
			Density 2.69, Hardness 4.5, Elements {'Ba': 27.05, 'Al': 10.63, 'Si': 16.6, 'H': 1.59, 'O': 44.13}
			Density 2.69, Hardness 4.5, Elements {'Ba': 27.05, 'Al': 10.63, 'Si': 16.6, 'H': 1.59, 'O': 44.13}
	Found duplicates of "Antimonpearceite", with these properties :
			Density 6.34, Hardness 3.0, Elements {'Cu': 11.98, 'Ag': 61.02, 'Sb': 8.61, 'As': 1.77, 'S': 16.63}
			Density 6.34, Hardness 3.0, Elements {'Cu': 11.98, 'Ag': 61.02, 'Sb': 8.61, 'As': 1.77, 'S': 16.63}
	Found duplicates of "Antimonselite", with these properties :
			Density 5.88, Hardness 3.5, Elements {'Sb': 50.69, 'Se': 49.31}
			Density 5.88, Hardness 3.5, Elements {'Sb': 50.69, 'Se': 49.31}
	Found duplicates of "Dyscrasite", with these properties :
			Density 9.69, Hardness 3.75, Elements {'Ag': 72.66, 'Sb': 27.34}
			Density 9.69, Hardness 3.75, Elements {'Ag': 72.66, 'Sb': 27.34}
	Found duplicates of "Romeite", with these properties :
			Density 5.05, Hardness 5.75, Elements {'Na': 0.56, 'Ca': 10.75, 'Ti': 5.84, 'Mn': 2.68, 'Fe': 8.17, 'Sb': 44.54, 'H': 0.07, 'O': 26.92, 'F': 0.46}
			Density 5.05, Hardness 5.75, Elements {'Na': 0.56, 'Ca': 10.75, 'Ti': 5.84, 'Mn': 2.68, 'Fe': 8.17, 'Sb': 44.54, 'H': 0.07, 'O': 26.92, 'F': 0.46}
			Density 5.05, Hardness 5.75, Elements {'Na': 0.56, 'Ca': 10.75, 'Ti': 5.84, 'Mn': 2.68, 'Fe': 8.17, 'Sb': 44.54, 'H': 0.07, 'O': 26.92, 'F': 0.46}
	Found duplicates of "Apatite-CaCl", with these properties :
			Density 3.15, Hardness 5.0, Elements {'Ca': 38.48, 'P': 17.84, 'Cl': 6.81, 'O': 36.87}
			Density 3.15, Hardness 5.0, Elements {'Ca': 38.48, 'P': 17.84, 'Cl': 6.81, 'O': 36.87}
	Found duplicates of "Apatite-CaOH-M", with these properties :
			Density None, Hardness 5.0, Elements {'Na': 2.44, 'Sr': 0.26, 'Ca': 35.82, 'La': 0.06, 'Ce': 0.14, 'Si': 0.09, 'P': 14.5, 'H': 0.16, 'S': 4.01, 'Cl': 1.64, 'O': 40.88}
			Density None, Hardness 5.0, Elements {'Na': 2.44, 'Sr': 0.26, 'Ca': 35.82, 'La': 0.06, 'Ce': 0.14, 'Si': 0.09, 'P': 14.5, 'H': 0.16, 'S': 4.01, 'Cl': 1.64, 'O': 40.88}
			Density None, Hardness 5.0, Elements {'Na': 2.44, 'Sr': 0.26, 'Ca': 35.82, 'La': 0.06, 'Ce': 0.14, 'Si': 0.09, 'P': 14.5, 'H': 0.16, 'S': 4.01, 'Cl': 1.64, 'O': 40.88}
	Found duplicates of "Apatite", with these properties :
			Density 3.19, Hardness 5.0, Elements {'Ca': 39.36, 'P': 18.25, 'H': 0.07, 'Cl': 2.32, 'O': 38.76, 'F': 1.24}
			Density 3.19, Hardness 5.0, Elements {'Ca': 39.36, 'P': 18.25, 'H': 0.07, 'Cl': 2.32, 'O': 38.76, 'F': 1.24}
			Density 3.19, Hardness 5.0, Elements {'Ca': 39.36, 'P': 18.25, 'H': 0.07, 'Cl': 2.32, 'O': 38.76, 'F': 1.24}
	Found duplicates of "Aqualite", with these properties :
			Density None, Hardness None, Elements {'K': 2.09, 'Na': 2.45, 'Sr': 1.56, 'Ca': 8.55, 'Zr': 9.73, 'Si': 25.96, 'H': 1.18, 'Cl': 1.26, 'O': 47.22}
			Density None, Hardness None, Elements {'K': 2.09, 'Na': 2.45, 'Sr': 1.56, 'Ca': 8.55, 'Zr': 9.73, 'Si': 25.96, 'H': 1.18, 'Cl': 1.26, 'O': 47.22}
	Found duplicates of "Arakiite", with these properties :
			Density None, Hardness 3.5, Elements {'Mg': 7.86, 'Mn': 27.6, 'Al': 1.3, 'Zn': 3.74, 'Fe': 5.0, 'As': 15.47, 'H': 1.6, 'O': 37.44}
			Density None, Hardness 3.5, Elements {'Mg': 7.86, 'Mn': 27.6, 'Al': 1.3, 'Zn': 3.74, 'Fe': 5.0, 'As': 15.47, 'H': 1.6, 'O': 37.44}
	Found duplicates of "Arapovite", with these properties :
			Density None, Hardness 5.75, Elements {'K': 3.7, 'Na': 1.87, 'Ca': 5.76, 'Eu': 0.17, 'La': 0.15, 'Ce': 0.47, 'Sm': 0.17, 'Dy': 0.18, 'Th': 9.31, 'U': 14.58, 'Si': 25.03, 'H': 0.2, 'Pb': 0.69, 'Nd': 0.48, 'O': 37.23}
			Density None, Hardness 5.75, Elements {'K': 3.7, 'Na': 1.87, 'Ca': 5.76, 'Eu': 0.17, 'La': 0.15, 'Ce': 0.47, 'Sm': 0.17, 'Dy': 0.18, 'Th': 9.31, 'U': 14.58, 'Si': 25.03, 'H': 0.2, 'Pb': 0.69, 'Nd': 0.48, 'O': 37.23}
	Found duplicates of "Ardennite-As", with these properties :
			Density 3.68, Hardness 6.5, Elements {'Ca': 3.09, 'Mg': 3.28, 'Mn': 14.85, 'Al': 12.24, 'V': 0.49, 'Fe': 2.16, 'Si': 13.55, 'As': 6.51, 'H': 0.58, 'O': 43.24}
			Density 3.68, Hardness 6.5, Elements {'Ca': 3.09, 'Mg': 3.28, 'Mn': 14.85, 'Al': 12.24, 'V': 0.49, 'Fe': 2.16, 'Si': 13.55, 'As': 6.51, 'H': 0.58, 'O': 43.24}
			Density 3.68, Hardness 6.5, Elements {'Ca': 3.09, 'Mg': 3.28, 'Mn': 14.85, 'Al': 12.24, 'V': 0.49, 'Fe': 2.16, 'Si': 13.55, 'As': 6.51, 'H': 0.58, 'O': 43.24}
	Found duplicates of "Ardennite-V", with these properties :
			Density None, Hardness 6.5, Elements {'Na': 0.01, 'Ca': 3.05, 'Mg': 2.7, 'Ti': 0.13, 'Mn': 18.17, 'Al': 12.05, 'V': 2.6, 'Cr': 0.24, 'Fe': 1.15, 'Si': 14.7, 'As': 0.24, 'P': 0.17, 'H': 0.64, 'O': 43.97, 'F': 0.17}
			Density None, Hardness 6.5, Elements {'Na': 0.01, 'Ca': 3.05, 'Mg': 2.7, 'Ti': 0.13, 'Mn': 18.17, 'Al': 12.05, 'V': 2.6, 'Cr': 0.24, 'Fe': 1.15, 'Si': 14.7, 'As': 0.24, 'P': 0.17, 'H': 0.64, 'O': 43.97, 'F': 0.17}
	Found duplicates of "Arhbarite", with these properties :
			Density None, Hardness None, Elements {'Mg': 6.13, 'Co': 0.17, 'Cu': 37.67, 'Si': 0.08, 'Ni': 0.17, 'As': 22.32, 'H': 0.84, 'O': 32.63}
			Density None, Hardness None, Elements {'Mg': 6.13, 'Co': 0.17, 'Cu': 37.67, 'Si': 0.08, 'Ni': 0.17, 'As': 22.32, 'H': 0.84, 'O': 32.63}
	Found duplicates of "Armbrusterite", with these properties :
			Density 2.78, Hardness 3.5, Elements {'K': 5.17, 'Na': 3.96, 'Ca': 0.19, 'Mg': 0.11, 'Ti': 0.03, 'Mn': 20.02, 'Al': 0.02, 'Zn': 0.15, 'Fe': 0.51, 'Si': 26.56, 'H': 0.47, 'O': 42.81}
			Density 2.78, Hardness 3.5, Elements {'K': 5.17, 'Na': 3.96, 'Ca': 0.19, 'Mg': 0.11, 'Ti': 0.03, 'Mn': 20.02, 'Al': 0.02, 'Zn': 0.15, 'Fe': 0.51, 'Si': 26.56, 'H': 0.47, 'O': 42.81}
	Found duplicates of "Armenite", with these properties :
			Density 2.76, Hardness 7.5, Elements {'Ba': 11.96, 'Ca': 6.98, 'Al': 14.1, 'Si': 22.02, 'H': 0.35, 'O': 44.59}
			Density 2.76, Hardness 7.5, Elements {'Ba': 11.96, 'Ca': 6.98, 'Al': 14.1, 'Si': 22.02, 'H': 0.35, 'O': 44.59}
	Found duplicates of "Arrojadite-BaFe", with these properties :
			Density 3.54, Hardness None, Elements {'K': 0.17, 'Ba': 4.9, 'Na': 3.08, 'Sr': 0.78, 'Ca': 1.43, 'Mg': 6.18, 'Mn': 0.49, 'Al': 1.2, 'Fe': 20.18, 'P': 16.58, 'H': 0.09, 'Pb': 9.24, 'O': 35.68}
			Density 3.54, Hardness None, Elements {'K': 0.17, 'Ba': 4.9, 'Na': 3.08, 'Sr': 0.78, 'Ca': 1.43, 'Mg': 6.18, 'Mn': 0.49, 'Al': 1.2, 'Fe': 20.18, 'P': 16.58, 'H': 0.09, 'Pb': 9.24, 'O': 35.68}
			Density None, Hardness None, Elements {'K': 0.17, 'Ba': 4.9, 'Na': 3.08, 'Sr': 0.78, 'Ca': 1.43, 'Mg': 6.18, 'Mn': 0.49, 'Al': 1.2, 'Fe': 20.18, 'P': 16.58, 'H': 0.09, 'Pb': 9.24, 'O': 35.68}
			Density 3.54, Hardness None, Elements {'K': 0.17, 'Ba': 4.9, 'Na': 3.08, 'Sr': 0.78, 'Ca': 1.43, 'Mg': 6.18, 'Mn': 0.49, 'Al': 1.2, 'Fe': 20.18, 'P': 16.58, 'H': 0.09, 'Pb': 9.24, 'O': 35.68}
	Found duplicates of "Arrojadite-KNa", with these properties :
			Density None, Hardness None, Elements {'K': 1.6, 'Na': 5.67, 'Sr': 0.04, 'Li': 0.0, 'Ca': 1.79, 'Mg': 3.22, 'Ti': 0.05, 'Mn': 2.78, 'Al': 1.38, 'Zn': 0.03, 'Fe': 25.66, 'Si': 0.01, 'P': 18.27, 'H': 0.15, 'O': 39.33, 'F': 0.03}
			Density None, Hardness None, Elements {'K': 1.6, 'Na': 5.67, 'Sr': 0.04, 'Li': 0.0, 'Ca': 1.79, 'Mg': 3.22, 'Ti': 0.05, 'Mn': 2.78, 'Al': 1.38, 'Zn': 0.03, 'Fe': 25.66, 'Si': 0.01, 'P': 18.27, 'H': 0.15, 'O': 39.33, 'F': 0.03}
	Found duplicates of "Arrojadite-PbFe", with these properties :
			Density None, Hardness 4.5, Elements {'Na': 2.05, 'Ca': 1.79, 'Mg': 1.08, 'Mn': 9.8, 'Al': 1.2, 'Fe': 22.41, 'P': 16.57, 'H': 0.11, 'Pb': 9.24, 'O': 35.31, 'F': 0.42}
			Density None, Hardness 4.5, Elements {'Na': 2.05, 'Ca': 1.79, 'Mg': 1.08, 'Mn': 9.8, 'Al': 1.2, 'Fe': 22.41, 'P': 16.57, 'H': 0.11, 'Pb': 9.24, 'O': 35.31, 'F': 0.42}
	Found duplicates of "Arrojadite-SrFe", with these properties :
			Density None, Hardness None, Elements {'K': 0.06, 'Ba': 1.33, 'Na': 3.56, 'Sr': 3.95, 'Li': 0.0, 'Ca': 1.15, 'Mg': 4.25, 'Sc': 0.09, 'Mn': 8.86, 'Al': 1.31, 'Zn': 0.22, 'Fe': 17.97, 'Si': 0.03, 'P': 17.93, 'H': 0.1, 'Pb': 0.3, 'O': 38.06, 'F': 0.83}
			Density None, Hardness None, Elements {'K': 0.06, 'Ba': 1.33, 'Na': 3.56, 'Sr': 3.95, 'Li': 0.0, 'Ca': 1.15, 'Mg': 4.25, 'Sc': 0.09, 'Mn': 8.86, 'Al': 1.31, 'Zn': 0.22, 'Fe': 17.97, 'Si': 0.03, 'P': 17.93, 'H': 0.1, 'Pb': 0.3, 'O': 38.06, 'F': 0.83}
	Found duplicates of "Arsenobismite", with these properties :
			Density 5.7, Hardness 3.0, Elements {'Bi': 68.75, 'As': 12.32, 'H': 0.5, 'O': 18.42}
			Density 5.7, Hardness 3.0, Elements {'Bi': 68.75, 'As': 12.32, 'H': 0.5, 'O': 18.42}
	Found duplicates of "Arsenovanmeersscheite", with these properties :
			Density None, Hardness None, Elements {'U': 63.47, 'As': 9.99, 'H': 0.94, 'O': 25.6}
			Density None, Hardness None, Elements {'U': 63.47, 'As': 9.99, 'H': 0.94, 'O': 25.6}
	Found duplicates of "Artinite", with these properties :
			Density 2.01, Hardness 2.5, Elements {'Mg': 24.72, 'H': 4.1, 'C': 6.11, 'O': 65.08}
			Density 2.01, Hardness 2.5, Elements {'Mg': 24.72, 'H': 4.1, 'C': 6.11, 'O': 65.08}
	Found duplicates of "Artroeite", with these properties :
			Density 5.39, Hardness 2.5, Elements {'Al': 8.3, 'H': 0.62, 'Pb': 63.72, 'O': 9.84, 'F': 17.53}
			Density 5.39, Hardness 2.5, Elements {'Al': 8.3, 'H': 0.62, 'Pb': 63.72, 'O': 9.84, 'F': 17.53}
	Found duplicates of "Artsmithite", with these properties :
			Density None, Hardness None, Elements {'Al': 2.63, 'Hg': 78.29, 'P': 5.26, 'H': 0.18, 'O': 13.64}
			Density None, Hardness None, Elements {'Al': 2.63, 'Hg': 78.29, 'P': 5.26, 'H': 0.18, 'O': 13.64}
	Found duplicates of "Atencioite", with these properties :
			Density 2.84, Hardness 4.5, Elements {'Ca': 7.07, 'Mg': 5.14, 'Mn': 0.98, 'Be': 3.34, 'Al': 0.2, 'Fe': 13.17, 'P': 17.53, 'H': 1.47, 'O': 51.09}
			Density 2.84, Hardness 4.5, Elements {'Ca': 7.07, 'Mg': 5.14, 'Mn': 0.98, 'Be': 3.34, 'Al': 0.2, 'Fe': 13.17, 'P': 17.53, 'H': 1.47, 'O': 51.09}
	Found duplicates of "Attakolite", with these properties :
			Density 3.16, Hardness 5.0, Elements {'Sr': 2.62, 'Ca': 4.8, 'Mn': 8.22, 'Al': 14.94, 'Fe': 2.51, 'Si': 2.94, 'P': 15.3, 'H': 0.75, 'O': 47.9}
			Density 3.16, Hardness 5.0, Elements {'Sr': 2.62, 'Ca': 4.8, 'Mn': 8.22, 'Al': 14.94, 'Fe': 2.51, 'Si': 2.94, 'P': 15.3, 'H': 0.75, 'O': 47.9}
	Found duplicates of "Attikaite", with these properties :
			Density 3.2, Hardness 2.25, Elements {'Ca': 12.47, 'Mg': 0.1, 'Al': 5.63, 'Fe': 0.12, 'Cu': 12.98, 'As': 29.66, 'P': 0.39, 'H': 0.85, 'S': 0.54, 'O': 37.26}
			Density 3.2, Hardness 2.25, Elements {'Ca': 12.47, 'Mg': 0.1, 'Al': 5.63, 'Fe': 0.12, 'Cu': 12.98, 'As': 29.66, 'P': 0.39, 'H': 0.85, 'S': 0.54, 'O': 37.26}
	Found duplicates of "Augite", with these properties :
			Density 3.4, Hardness 5.75, Elements {'Na': 0.97, 'Ca': 15.26, 'Mg': 9.26, 'Ti': 2.03, 'Al': 4.57, 'Fe': 4.73, 'Si': 22.58, 'O': 40.62}
			Density 3.4, Hardness 5.75, Elements {'Na': 0.97, 'Ca': 15.26, 'Mg': 9.26, 'Ti': 2.03, 'Al': 4.57, 'Fe': 4.73, 'Si': 22.58, 'O': 40.62}
	Found duplicates of "Auricupride", with these properties :
			Density 11.5, Hardness 2.5, Elements {'Cu': 49.18, 'Au': 50.82}
			Density 11.5, Hardness 2.5, Elements {'Cu': 49.18, 'Au': 50.82}
	Found duplicates of "Aurivilliusite", with these properties :
			Density None, Hardness None, Elements {'Hg': 74.07, 'I': 22.73, 'Br': 0.15, 'Cl': 0.07, 'O': 2.98}
			Density None, Hardness None, Elements {'Hg': 74.07, 'I': 22.73, 'Br': 0.15, 'Cl': 0.07, 'O': 2.98}
	Found duplicates of "Avdoninite", with these properties :
			Density 3.03, Hardness 3.0, Elements {'K': 10.01, 'Cu': 41.5, 'H': 0.78, 'Cl': 37.46, 'O': 10.24}
			Density 3.03, Hardness 3.0, Elements {'K': 10.01, 'Cu': 41.5, 'H': 0.78, 'Cl': 37.46, 'O': 10.24}
	Found duplicates of "Averievite", with these properties :
			Density 3.75, Hardness 4.0, Elements {'V': 14.27, 'Cu': 53.4, 'Cl': 9.93, 'O': 22.41}
			Density 3.75, Hardness 4.0, Elements {'V': 14.27, 'Cu': 53.4, 'Cl': 9.93, 'O': 22.41}
	Found duplicates of "Axinite-Fe", with these properties :
			Density 3.28, Hardness 6.75, Elements {'Ca': 14.06, 'Al': 9.47, 'Fe': 9.8, 'Si': 19.71, 'B': 1.9, 'H': 0.18, 'O': 44.9}
			Density 3.28, Hardness 6.75, Elements {'Ca': 14.06, 'Al': 9.47, 'Fe': 9.8, 'Si': 19.71, 'B': 1.9, 'H': 0.18, 'O': 44.9}
	Found duplicates of "Santabarbaraite", with these properties :
			Density 2.42, Hardness None, Elements {'Mg': 0.34, 'Mn': 1.56, 'Fe': 30.67, 'P': 12.87, 'H': 2.67, 'O': 51.88}
			Density 2.42, Hardness None, Elements {'Mg': 0.34, 'Mn': 1.56, 'Fe': 30.67, 'P': 12.87, 'H': 2.67, 'O': 51.88}
			Density 2.42, Hardness None, Elements {'Mg': 0.34, 'Mn': 1.56, 'Fe': 30.67, 'P': 12.87, 'H': 2.67, 'O': 51.88}
	Found duplicates of "Azurite", with these properties :
			Density 3.83, Hardness 3.75, Elements {'Cu': 55.31, 'H': 0.58, 'C': 6.97, 'O': 37.14}
			Density 3.83, Hardness 3.75, Elements {'Cu': 55.31, 'H': 0.58, 'C': 6.97, 'O': 37.14}
	Found duplicates of "Babkinite", with these properties :
			Density 8.09, Hardness 2.0, Elements {'Bi': 42.85, 'Pb': 42.48, 'Se': 8.09, 'S': 6.57}
			Density 8.09, Hardness 2.0, Elements {'Bi': 42.85, 'Pb': 42.48, 'Se': 8.09, 'S': 6.57}
	Found duplicates of "Cordylite-Ce", with these properties :
			Density 4.44, Hardness 4.5, Elements {'Ba': 21.63, 'La': 10.94, 'Ce': 33.1, 'C': 5.67, 'O': 22.68, 'F': 5.98}
			Density 4.44, Hardness 4.5, Elements {'Ba': 21.63, 'La': 10.94, 'Ce': 33.1, 'C': 5.67, 'O': 22.68, 'F': 5.98}
			Density 4.44, Hardness 4.5, Elements {'Ba': 21.63, 'La': 10.94, 'Ce': 33.1, 'C': 5.67, 'O': 22.68, 'F': 5.98}
	Found duplicates of "Bakhchisaraitsevite", with these properties :
			Density 2.5, Hardness 2.25, Elements {'Na': 6.83, 'Mg': 18.04, 'P': 18.4, 'H': 2.1, 'O': 54.64}
			Density 2.5, Hardness 2.25, Elements {'Na': 6.83, 'Mg': 18.04, 'P': 18.4, 'H': 2.1, 'O': 54.64}
	Found duplicates of "Baksanite", with these properties :
			Density 8.1, Hardness 1.75, Elements {'Bi': 78.11, 'Te': 15.9, 'S': 5.99}
			Density 8.1, Hardness 1.75, Elements {'Bi': 78.11, 'Te': 15.9, 'S': 5.99}
	Found duplicates of "Spinel", with these properties :
			Density 3.64, Hardness 8.0, Elements {'Mg': 17.08, 'Al': 37.93, 'O': 44.98}
			Density 3.64, Hardness 8.0, Elements {'Mg': 17.08, 'Al': 37.93, 'O': 44.98}
			Density 3.64, Hardness 8.0, Elements {'Mg': 17.08, 'Al': 37.93, 'O': 44.98}
	Found duplicates of "Barahonaite-Al", with these properties :
			Density None, Hardness None, Elements {'Na': 1.22, 'Ca': 11.23, 'Al': 5.05, 'Fe': 0.08, 'Cu': 11.67, 'Si': 0.11, 'As': 27.45, 'P': 0.12, 'H': 1.62, 'S': 0.5, 'Cl': 0.07, 'O': 40.88}
			Density None, Hardness None, Elements {'Na': 1.22, 'Ca': 11.23, 'Al': 5.05, 'Fe': 0.08, 'Cu': 11.67, 'Si': 0.11, 'As': 27.45, 'P': 0.12, 'H': 1.62, 'S': 0.5, 'Cl': 0.07, 'O': 40.88}
	Found duplicates of "Barahonaite-Fe", with these properties :
			Density 3.03, Hardness None, Elements {'Na': 1.54, 'Ca': 9.26, 'Mg': 0.08, 'Al': 0.91, 'Fe': 9.65, 'Cu': 9.91, 'Si': 0.1, 'As': 26.87, 'P': 0.23, 'H': 1.56, 'S': 0.12, 'Cl': 0.91, 'O': 38.86}
			Density 3.03, Hardness None, Elements {'Na': 1.54, 'Ca': 9.26, 'Mg': 0.08, 'Al': 0.91, 'Fe': 9.65, 'Cu': 9.91, 'Si': 0.1, 'As': 26.87, 'P': 0.23, 'H': 1.56, 'S': 0.12, 'Cl': 0.91, 'O': 38.86}
	Found duplicates of "Bario-olgite", with these properties :
			Density 4.0, Hardness 4.25, Elements {'K': 0.7, 'Ba': 28.23, 'Na': 10.99, 'Sr': 14.1, 'Ca': 0.18, 'La': 1.86, 'Ce': 1.25, 'Mn': 0.25, 'P': 13.84, 'O': 28.6}
			Density 4.0, Hardness 4.25, Elements {'K': 0.7, 'Ba': 28.23, 'Na': 10.99, 'Sr': 14.1, 'Ca': 0.18, 'La': 1.86, 'Ce': 1.25, 'Mn': 0.25, 'P': 13.84, 'O': 28.6}
			Density 4.0, Hardness 4.25, Elements {'K': 0.7, 'Ba': 28.23, 'Na': 10.99, 'Sr': 14.1, 'Ca': 0.18, 'La': 1.86, 'Ce': 1.25, 'Mn': 0.25, 'P': 13.84, 'O': 28.6}
			Density 4.0, Hardness 4.25, Elements {'K': 0.7, 'Ba': 28.23, 'Na': 10.99, 'Sr': 14.1, 'Ca': 0.18, 'La': 1.86, 'Ce': 1.25, 'Mn': 0.25, 'P': 13.84, 'O': 28.6}
	Found duplicates of "Bariomicrolite", with these properties :
			Density 5.68, Hardness 4.75, Elements {'Ba': 8.14, 'Ta': 64.33, 'Nb': 3.67, 'H': 0.8, 'O': 23.07}
			Density 5.68, Hardness 4.75, Elements {'Ba': 8.14, 'Ta': 64.33, 'Nb': 3.67, 'H': 0.8, 'O': 23.07}
	Found duplicates of "Barioperovskite", with these properties :
			Density None, Hardness None, Elements {'Ba': 58.18, 'Ti': 20.49, 'Si': 0.37, 'O': 20.96}
			Density None, Hardness None, Elements {'Ba': 58.18, 'Ti': 20.49, 'Si': 0.37, 'O': 20.96}
	Found duplicates of "Bariopharmacosiderite", with these properties :
			Density None, Hardness 3.0, Elements {'Ba': 14.42, 'Fe': 23.45, 'As': 23.6, 'H': 1.59, 'O': 36.95}
			Density None, Hardness 3.0, Elements {'Ba': 14.42, 'Fe': 23.45, 'As': 23.6, 'H': 1.59, 'O': 36.95}
	Found duplicates of "Bariosincosite", with these properties :
			Density None, Hardness 3.0, Elements {'Ba': 25.75, 'V': 19.11, 'P': 11.62, 'H': 1.51, 'O': 42.01}
			Density None, Hardness 3.0, Elements {'Ba': 25.75, 'V': 19.11, 'P': 11.62, 'H': 1.51, 'O': 42.01}
	Found duplicates of "Brewsterite-Ba", with these properties :
			Density 2.453, Hardness 5.0, Elements {'Ba': 14.85, 'Sr': 3.16, 'Al': 7.78, 'Si': 24.3, 'H': 1.45, 'O': 48.45}
			Density 2.453, Hardness 5.0, Elements {'Ba': 14.85, 'Sr': 3.16, 'Al': 7.78, 'Si': 24.3, 'H': 1.45, 'O': 48.45}
	Found duplicates of "Barquillite", with these properties :
			Density None, Hardness 4.25, Elements {'Cd': 25.53, 'Cu': 28.86, 'Ge': 16.49, 'S': 29.13}
			Density None, Hardness 4.25, Elements {'Cd': 25.53, 'Cu': 28.86, 'Ge': 16.49, 'S': 29.13}
	Found duplicates of "Batiferrite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 0.18, 'Ba': 10.9, 'Na': 0.11, 'Sr': 0.41, 'Mg': 0.91, 'Ti': 8.05, 'Mn': 2.05, 'Fe': 49.01, 'O': 28.38}
			Density None, Hardness 6.0, Elements {'K': 0.18, 'Ba': 10.9, 'Na': 0.11, 'Sr': 0.41, 'Mg': 0.91, 'Ti': 8.05, 'Mn': 2.05, 'Fe': 49.01, 'O': 28.38}
	Found duplicates of "Batisivite", with these properties :
			Density None, Hardness None, Elements {'Ba': 10.54, 'Mg': 0.04, 'Ti': 20.05, 'Nb': 0.22, 'Al': 1.0, 'V': 22.01, 'Cr': 8.26, 'Fe': 1.05, 'Si': 2.85, 'O': 34.0}
			Density None, Hardness None, Elements {'Ba': 10.54, 'Mg': 0.04, 'Ti': 20.05, 'Nb': 0.22, 'Al': 1.0, 'V': 22.01, 'Cr': 8.26, 'Fe': 1.05, 'Si': 2.85, 'O': 34.0}
	Found duplicates of "Baumstarkite", with these properties :
			Density 5.33, Hardness 2.5, Elements {'Ag': 37.22, 'Sb': 38.5, 'As': 2.15, 'S': 22.13}
			Density None, Hardness None, Elements {'Ag': 37.22, 'Sb': 38.5, 'As': 2.15, 'S': 22.13}
	Found duplicates of "Bechererite", with these properties :
			Density 3.47, Hardness 2.75, Elements {'Zn': 45.53, 'Cu': 10.21, 'Si': 1.5, 'H': 1.62, 'S': 5.15, 'O': 35.99}
			Density 3.47, Hardness 2.75, Elements {'Zn': 45.53, 'Cu': 10.21, 'Si': 1.5, 'H': 1.62, 'S': 5.15, 'O': 35.99}
	Found duplicates of "Bederite", with these properties :
			Density 3.48, Hardness 5.0, Elements {'Na': 0.58, 'Ca': 8.12, 'Mg': 2.46, 'Mn': 16.7, 'Al': 0.55, 'Fe': 10.19, 'P': 18.83, 'H': 0.41, 'O': 42.15}
			Density 3.48, Hardness 5.0, Elements {'Na': 0.58, 'Ca': 8.12, 'Mg': 2.46, 'Mn': 16.7, 'Al': 0.55, 'Fe': 10.19, 'P': 18.83, 'H': 0.41, 'O': 42.15}
	Found duplicates of "Schirmerite", with these properties :
			Density 6.74, Hardness 2.0, Elements {'Ag': 10.84, 'Bi': 49.0, 'Pb': 20.82, 'S': 19.33}
			Density 6.74, Hardness 2.0, Elements {'Ag': 10.84, 'Bi': 49.0, 'Pb': 20.82, 'S': 19.33}
	Found duplicates of "Crocoite", with these properties :
			Density 6.0, Hardness 2.75, Elements {'Cr': 16.09, 'Pb': 64.11, 'O': 19.8}
			Density 6.0, Hardness 2.75, Elements {'Cr': 16.09, 'Pb': 64.11, 'O': 19.8}
	Found duplicates of "Belloite", with these properties :
			Density None, Hardness 1.5, Elements {'Cu': 54.78, 'H': 0.87, 'Cl': 30.56, 'O': 13.79}
			Density None, Hardness 1.5, Elements {'Cu': 54.78, 'H': 0.87, 'Cl': 30.56, 'O': 13.79}
	Found duplicates of "Belovite-La", with these properties :
			Density 4.19, Hardness 5.0, Elements {'Sr': 22.74, 'Ca': 1.16, 'La': 24.03, 'Ce': 16.16, 'P': 10.72, 'H': 0.12, 'O': 23.99, 'F': 1.1}
			Density 4.19, Hardness 5.0, Elements {'Sr': 22.74, 'Ca': 1.16, 'La': 24.03, 'Ce': 16.16, 'P': 10.72, 'H': 0.12, 'O': 23.99, 'F': 1.1}
			Density 4.19, Hardness 5.0, Elements {'Sr': 22.74, 'Ca': 1.16, 'La': 24.03, 'Ce': 16.16, 'P': 10.72, 'H': 0.12, 'O': 23.99, 'F': 1.1}
	Found duplicates of "Benauite", with these properties :
			Density 3.65, Hardness 3.5, Elements {'Ba': 4.55, 'Sr': 10.17, 'Fe': 26.85, 'P': 7.7, 'H': 1.39, 'Pb': 3.44, 'S': 2.66, 'O': 43.24}
			Density 3.65, Hardness 3.5, Elements {'Ba': 4.55, 'Sr': 10.17, 'Fe': 26.85, 'P': 7.7, 'H': 1.39, 'Pb': 3.44, 'S': 2.66, 'O': 43.24}
	Found duplicates of "Bendadaite", with these properties :
			Density None, Hardness None, Elements {'Fe': 30.38, 'As': 27.17, 'H': 1.83, 'O': 40.62}
			Density None, Hardness None, Elements {'Fe': 30.38, 'As': 27.17, 'H': 1.83, 'O': 40.62}
	Found duplicates of "Benyacarite", with these properties :
			Density 2.4, Hardness 2.75, Elements {'K': 1.22, 'Na': 0.24, 'Mg': 0.25, 'Ti': 7.98, 'Mn': 8.59, 'Al': 0.28, 'Fe': 10.48, 'P': 12.91, 'H': 3.11, 'O': 53.35, 'F': 1.58}
			Density 2.4, Hardness 2.75, Elements {'K': 1.22, 'Na': 0.24, 'Mg': 0.25, 'Ti': 7.98, 'Mn': 8.59, 'Al': 0.28, 'Fe': 10.48, 'P': 12.91, 'H': 3.11, 'O': 53.35, 'F': 1.58}
	Found duplicates of "Berezanskite", with these properties :
			Density 2.66, Hardness 2.75, Elements {'K': 4.02, 'Li': 2.14, 'Ti': 9.84, 'Si': 34.65, 'O': 49.35}
			Density 2.66, Hardness 2.75, Elements {'K': 4.02, 'Li': 2.14, 'Ti': 9.84, 'Si': 34.65, 'O': 49.35}
	Found duplicates of "Duftite-beta", with these properties :
			Density 6.4, Hardness 3.0, Elements {'Cu': 14.89, 'As': 17.56, 'H': 0.24, 'Pb': 48.56, 'O': 18.75}
			Density 6.4, Hardness 3.0, Elements {'Cu': 14.89, 'As': 17.56, 'H': 0.24, 'Pb': 48.56, 'O': 18.75}
	Found duplicates of "Roselite-beta", with these properties :
			Density 3.71, Hardness 3.75, Elements {'Ca': 18.04, 'Mg': 1.37, 'Co': 9.95, 'As': 33.73, 'H': 0.91, 'O': 36.01}
			Density 3.71, Hardness 3.75, Elements {'Ca': 18.04, 'Mg': 1.37, 'Co': 9.95, 'As': 33.73, 'H': 0.91, 'O': 36.01}
	Found duplicates of "Bieberite", with these properties :
			Density 1.9, Hardness 2.0, Elements {'Co': 20.96, 'H': 5.02, 'S': 11.41, 'O': 62.61}
			Density 1.9, Hardness 2.0, Elements {'Co': 20.96, 'H': 5.02, 'S': 11.41, 'O': 62.61}
	Found duplicates of "Biehlite", with these properties :
			Density None, Hardness 1.25, Elements {'Sb': 51.44, 'Mo': 22.52, 'As': 3.52, 'O': 22.53}
			Density None, Hardness 1.25, Elements {'Sb': 51.44, 'Mo': 22.52, 'As': 3.52, 'O': 22.53}
	Found duplicates of "Bigcreekite", with these properties :
			Density 2.66, Hardness 2.5, Elements {'Ba': 39.74, 'Si': 16.26, 'H': 2.33, 'O': 41.67}
			Density 2.66, Hardness 2.5, Elements {'Ba': 39.74, 'Si': 16.26, 'H': 2.33, 'O': 41.67}
	Found duplicates of "Biraite-Ce", with these properties :
			Density None, Hardness 5.0, Elements {'Ba': 0.25, 'Na': 0.08, 'Ca': 0.51, 'La': 14.37, 'Ce': 25.68, 'Pr': 2.3, 'Sm': 0.55, 'Mg': 1.1, 'Ti': 0.09, 'Mn': 1.1, 'Fe': 6.08, 'Si': 10.04, 'C': 2.16, 'Nd': 6.54, 'O': 28.57, 'F': 0.59}
			Density None, Hardness 5.0, Elements {'Ba': 0.25, 'Na': 0.08, 'Ca': 0.51, 'La': 14.37, 'Ce': 25.68, 'Pr': 2.3, 'Sm': 0.55, 'Mg': 1.1, 'Ti': 0.09, 'Mn': 1.1, 'Fe': 6.08, 'Si': 10.04, 'C': 2.16, 'Nd': 6.54, 'O': 28.57, 'F': 0.59}
	Found duplicates of "Birchite", with these properties :
			Density 3.61, Hardness 3.75, Elements {'Ca': 0.11, 'Mn': 0.15, 'Zn': 0.88, 'Cd': 31.7, 'Cu': 16.64, 'P': 8.65, 'H': 1.36, 'S': 3.81, 'O': 36.7}
			Density 3.61, Hardness 3.75, Elements {'Ca': 0.11, 'Mn': 0.15, 'Zn': 0.88, 'Cd': 31.7, 'Cu': 16.64, 'P': 8.65, 'H': 1.36, 'S': 3.81, 'O': 36.7}
	Found duplicates of "Bismuthinite", with these properties :
			Density 7.0, Hardness 2.0, Elements {'Bi': 81.29, 'S': 18.71}
			Density 7.0, Hardness 2.0, Elements {'Bi': 81.29, 'S': 18.71}
	Found duplicates of "Bismutite", with these properties :
			Density 7.0, Hardness 4.0, Elements {'Bi': 81.96, 'C': 2.36, 'O': 15.69}
			Density 7.0, Hardness 4.0, Elements {'Bi': 81.96, 'C': 2.36, 'O': 15.69}
	Found duplicates of "Bismutopyrochlore", with these properties :
			Density 4.97, Hardness 5.0, Elements {'Ca': 2.14, 'U': 12.69, 'Ta': 8.04, 'Nb': 24.76, 'Bi': 14.85, 'H': 1.72, 'Pb': 3.68, 'O': 32.12}
			Density 4.97, Hardness 5.0, Elements {'Ca': 2.14, 'U': 12.69, 'Ta': 8.04, 'Nb': 24.76, 'Bi': 14.85, 'H': 1.72, 'Pb': 3.68, 'O': 32.12}
	Found duplicates of "Cosalite", with these properties :
			Density 6.6, Hardness 2.75, Elements {'Bi': 42.1, 'Pb': 41.75, 'S': 16.15}
			Density 6.6, Hardness 2.75, Elements {'Bi': 42.1, 'Pb': 41.75, 'S': 16.15}
	Found duplicates of "Brochantite", with these properties :
			Density 3.97, Hardness 3.75, Elements {'Cu': 56.2, 'H': 1.34, 'S': 7.09, 'O': 35.37}
			Density 3.97, Hardness 3.75, Elements {'Cu': 56.2, 'H': 1.34, 'S': 7.09, 'O': 35.37}
	Found duplicates of "Blatonite", with these properties :
			Density 4.01, Hardness 2.5, Elements {'U': 68.39, 'H': 0.58, 'C': 3.45, 'O': 27.58}
			Density 4.01, Hardness 2.5, Elements {'U': 68.39, 'H': 0.58, 'C': 3.45, 'O': 27.58}
	Found duplicates of "Bleasdaleite", with these properties :
			Density None, Hardness 2.0, Elements {'Ca': 6.12, 'Fe': 2.13, 'Cu': 31.54, 'Bi': 1.6, 'P': 11.83, 'H': 1.94, 'Cl': 1.02, 'O': 43.83}
			Density None, Hardness 2.0, Elements {'Ca': 6.12, 'Fe': 2.13, 'Cu': 31.54, 'Bi': 1.6, 'P': 11.83, 'H': 1.94, 'Cl': 1.02, 'O': 43.83}
	Found duplicates of "Blodite", with these properties :
			Density 2.23, Hardness 3.0, Elements {'Na': 13.75, 'Mg': 7.27, 'H': 2.41, 'S': 19.17, 'O': 57.4}
			Density 2.23, Hardness 3.0, Elements {'Na': 13.75, 'Mg': 7.27, 'H': 2.41, 'S': 19.17, 'O': 57.4}
	Found duplicates of "Diamond", with these properties :
			Density 3.51, Hardness 10.0, Elements {'C': 100.0}
			Density 3.51, Hardness 10.0, Elements {'C': 100.0}
			Density 3.51, Hardness 10.0, Elements {'C': 100.0}
			Density 3.51, Hardness 10.0, Elements {'C': 100.0}
	Found duplicates of "Bobjonesite", with these properties :
			Density None, Hardness 1.0, Elements {'V': 23.47, 'H': 2.79, 'S': 14.77, 'O': 58.97}
			Density None, Hardness 1.0, Elements {'V': 23.47, 'H': 2.79, 'S': 14.77, 'O': 58.97}
	Found duplicates of "Bobkingite", with these properties :
			Density None, Hardness 3.0, Elements {'Cu': 56.66, 'H': 2.16, 'Cl': 12.65, 'O': 28.53}
			Density None, Hardness 3.0, Elements {'Cu': 56.66, 'H': 2.16, 'Cl': 12.65, 'O': 28.53}
	Found duplicates of "Bobtraillite", with these properties :
			Density None, Hardness 5.5, Elements {'Ba': 0.35, 'Na': 4.08, 'Sr': 14.69, 'Ca': 0.77, 'Y': 0.89, 'Hf': 0.4, 'Zr': 18.32, 'Nb': 0.9, 'Si': 18.51, 'B': 1.03, 'H': 0.57, 'O': 39.5}
			Density None, Hardness 5.5, Elements {'Ba': 0.35, 'Na': 4.08, 'Sr': 14.69, 'Ca': 0.77, 'Y': 0.89, 'Hf': 0.4, 'Zr': 18.32, 'Nb': 0.9, 'Si': 18.51, 'B': 1.03, 'H': 0.57, 'O': 39.5}
	Found duplicates of "Boggildite", with these properties :
			Density 3.66, Hardness 4.5, Elements {'Na': 8.5, 'Sr': 32.38, 'Al': 9.97, 'P': 5.72, 'O': 11.83, 'F': 31.6}
			Density 3.66, Hardness 4.5, Elements {'Na': 8.5, 'Sr': 32.38, 'Al': 9.97, 'P': 5.72, 'O': 11.83, 'F': 31.6}
	Found duplicates of "Boralsilite", with these properties :
			Density 3.06, Hardness None, Elements {'Al': 37.71, 'Si': 4.91, 'B': 5.67, 'O': 51.71}
			Density 3.06, Hardness None, Elements {'Al': 37.71, 'Si': 4.91, 'B': 5.67, 'O': 51.71}
	Found duplicates of "Sassolite", with these properties :
			Density 3.4, Hardness 1.0, Elements {'B': 17.48, 'H': 4.89, 'O': 77.63}
			Density 3.4, Hardness 1.0, Elements {'B': 17.48, 'H': 4.89, 'O': 77.63}
	Found duplicates of "Delvauxite", with these properties :
			Density 1.9, Hardness 2.5, Elements {'Ca': 3.48, 'Mg': 0.7, 'Al': 0.62, 'Fe': 25.22, 'P': 10.04, 'H': 2.03, 'C': 0.28, 'S': 4.08, 'O': 53.54}
			Density 1.9, Hardness 2.5, Elements {'Ca': 3.48, 'Mg': 0.7, 'Al': 0.62, 'Fe': 25.22, 'P': 10.04, 'H': 2.03, 'C': 0.28, 'S': 4.08, 'O': 53.54}
	Found duplicates of "Borocookeite", with these properties :
			Density 2.62, Hardness 3.0, Elements {'Li': 2.21, 'Al': 20.39, 'Si': 16.76, 'B': 1.29, 'H': 1.56, 'O': 56.65, 'F': 1.13}
			Density 2.62, Hardness 3.0, Elements {'Li': 2.21, 'Al': 20.39, 'Si': 16.76, 'B': 1.29, 'H': 1.56, 'O': 56.65, 'F': 1.13}
	Found duplicates of "Boromullite", with these properties :
			Density None, Hardness None, Elements {'Mg': 0.04, 'Al': 39.25, 'Fe': 0.27, 'Si': 8.88, 'B': 2.02, 'O': 49.54}
			Density None, Hardness None, Elements {'Mg': 0.04, 'Al': 39.25, 'Fe': 0.27, 'Si': 8.88, 'B': 2.02, 'O': 49.54}
	Found duplicates of "Bortnikovite", with these properties :
			Density None, Hardness 4.75, Elements {'Zn': 8.1, 'Fe': 1.43, 'Cu': 27.55, 'Pd': 58.82, 'Pt': 4.09}
			Density None, Hardness 4.75, Elements {'Zn': 8.1, 'Fe': 1.43, 'Cu': 27.55, 'Pd': 58.82, 'Pt': 4.09}
	Found duplicates of "Botryogen", with these properties :
			Density 2.04, Hardness 2.0, Elements {'Mg': 5.85, 'Fe': 13.44, 'H': 3.64, 'S': 15.44, 'O': 61.63}
			Density 2.04, Hardness 2.0, Elements {'Mg': 5.85, 'Fe': 13.44, 'H': 3.64, 'S': 15.44, 'O': 61.63}
	Found duplicates of "Bouazzerite", with these properties :
			Density None, Hardness None, Elements {'Ca': 0.14, 'Mg': 3.14, 'Cr': 0.51, 'Fe': 10.77, 'Co': 0.43, 'Si': 0.14, 'Ni': 0.11, 'Bi': 19.65, 'As': 19.5, 'H': 2.84, 'O': 42.77}
			Density None, Hardness None, Elements {'Ca': 0.14, 'Mg': 3.14, 'Cr': 0.51, 'Fe': 10.77, 'Co': 0.43, 'Si': 0.14, 'Ni': 0.11, 'Bi': 19.65, 'As': 19.5, 'H': 2.84, 'O': 42.77}
	Found duplicates of "Bradaczekite", with these properties :
			Density None, Hardness None, Elements {'Na': 4.02, 'Zn': 0.95, 'Cu': 34.27, 'As': 32.76, 'O': 27.99}
			Density None, Hardness None, Elements {'Na': 4.02, 'Zn': 0.95, 'Cu': 34.27, 'As': 32.76, 'O': 27.99}
	Found duplicates of "Braithwaiteite", with these properties :
			Density None, Hardness None, Elements {'Na': 1.27, 'Ti': 2.74, 'Cu': 20.86, 'Sb': 11.6, 'As': 28.16, 'H': 1.15, 'O': 34.22}
			Density None, Hardness None, Elements {'Na': 1.27, 'Ti': 2.74, 'Cu': 20.86, 'Sb': 11.6, 'As': 28.16, 'H': 1.15, 'O': 34.22}
	Found duplicates of "Brandholzite", with these properties :
			Density None, Hardness None, Elements {'Mg': 2.37, 'Sb': 47.39, 'H': 3.53, 'O': 46.71}
			Density None, Hardness None, Elements {'Mg': 2.37, 'Sb': 47.39, 'H': 3.53, 'O': 46.71}
	Found duplicates of "Brendelite", with these properties :
			Density 6.83, Hardness 4.5, Elements {'Fe': 9.06, 'Bi': 43.05, 'P': 5.02, 'H': 0.16, 'Pb': 24.54, 'O': 18.17}
			Density 6.83, Hardness 4.5, Elements {'Fe': 9.06, 'Bi': 43.05, 'P': 5.02, 'H': 0.16, 'Pb': 24.54, 'O': 18.17}
	Found duplicates of "Brianroulstonite", with these properties :
			Density 1.97, Hardness 5.0, Elements {'Ca': 19.89, 'B': 8.94, 'H': 3.84, 'Cl': 11.73, 'O': 55.59}
			Density 1.97, Hardness 5.0, Elements {'Ca': 19.89, 'B': 8.94, 'H': 3.84, 'Cl': 11.73, 'O': 55.59}
	Found duplicates of "Brianyoungite", with these properties :
			Density 4.01, Hardness 2.25, Elements {'Zn': 58.87, 'H': 1.21, 'C': 2.7, 'S': 2.41, 'O': 34.81}
			Density 4.01, Hardness 2.25, Elements {'Zn': 58.87, 'H': 1.21, 'C': 2.7, 'S': 2.41, 'O': 34.81}
	Found duplicates of "Brinrobertsite", with these properties :
			Density None, Hardness None, Elements {'K': 0.29, 'Na': 0.53, 'Ca': 0.25, 'Mg': 0.2, 'Al': 11.18, 'Fe': 0.47, 'Si': 22.99, 'H': 3.06, 'O': 61.03}
			Density None, Hardness None, Elements {'K': 0.29, 'Na': 0.53, 'Ca': 0.25, 'Mg': 0.2, 'Al': 11.18, 'Fe': 0.47, 'Si': 22.99, 'H': 3.06, 'O': 61.03}
	Found duplicates of "Britvinite", with these properties :
			Density None, Hardness 3.0, Elements {'Mg': 4.78, 'Al': 0.22, 'Si': 5.96, 'B': 0.83, 'H': 0.26, 'Pb': 66.62, 'C': 0.57, 'O': 20.76}
			Density None, Hardness 3.0, Elements {'Mg': 4.78, 'Al': 0.22, 'Si': 5.96, 'B': 0.83, 'H': 0.26, 'Pb': 66.62, 'C': 0.57, 'O': 20.76}
	Found duplicates of "Brizziite-VII", with these properties :
			Density 4.87, Hardness 2.0, Elements {'Na': 11.93, 'Sb': 63.17, 'O': 24.9}
			Density 4.87, Hardness 2.0, Elements {'Na': 11.93, 'Sb': 63.17, 'O': 24.9}
	Found duplicates of "Brockite", with these properties :
			Density 3.9, Hardness 3.5, Elements {'Ca': 10.9, 'Ce': 6.35, 'Th': 31.55, 'P': 14.04, 'H': 0.91, 'O': 36.25}
			Density 3.9, Hardness 3.5, Elements {'Ca': 10.9, 'Ce': 6.35, 'Th': 31.55, 'P': 14.04, 'H': 0.91, 'O': 36.25}
			Density 3.9, Hardness 3.5, Elements {'Ca': 10.9, 'Ce': 6.35, 'Th': 31.55, 'P': 14.04, 'H': 0.91, 'O': 36.25}
	Found duplicates of "Brodtkorbite", with these properties :
			Density None, Hardness 2.75, Elements {'Cu': 26.17, 'Hg': 41.31, 'Se': 32.52}
			Density None, Hardness 2.75, Elements {'Cu': 26.17, 'Hg': 41.31, 'Se': 32.52}
	Found duplicates of "Bromargyrite", with these properties :
			Density 5.9, Hardness 1.75, Elements {'Ag': 57.45, 'Br': 42.55}
			Density 5.9, Hardness 1.75, Elements {'Ag': 57.45, 'Br': 42.55}
	Found duplicates of "Diaphorite", with these properties :
			Density 6.1, Hardness 2.5, Elements {'Ag': 23.8, 'Sb': 26.86, 'Pb': 30.48, 'S': 18.87}
			Density 6.1, Hardness 2.5, Elements {'Ag': 23.8, 'Sb': 26.86, 'Pb': 30.48, 'S': 18.87}
	Found duplicates of "Bruggenite", with these properties :
			Density 4.24, Hardness 3.5, Elements {'Ca': 9.83, 'H': 0.49, 'I': 62.22, 'O': 27.46}
			Density 4.24, Hardness 3.5, Elements {'Ca': 9.83, 'H': 0.49, 'I': 62.22, 'O': 27.46}
	Found duplicates of "Butschliite", with these properties :
			Density None, Hardness None, Elements {'K': 32.82, 'Ca': 16.82, 'C': 10.08, 'O': 40.29}
			Density None, Hardness None, Elements {'K': 32.82, 'Ca': 16.82, 'C': 10.08, 'O': 40.29}
	Found duplicates of "Burnsite", with these properties :
			Density None, Hardness 1.25, Elements {'K': 3.52, 'Cd': 9.08, 'Cu': 36.93, 'Se': 14.0, 'Cl': 25.18, 'O': 11.28}
			Density None, Hardness 1.25, Elements {'K': 3.52, 'Cd': 9.08, 'Cu': 36.93, 'Se': 14.0, 'Cl': 25.18, 'O': 11.28}
	Found duplicates of "Buryatite", with these properties :
			Density None, Hardness 2.5, Elements {'Ca': 16.46, 'Al': 0.37, 'Fe': 1.53, 'Si': 2.31, 'B': 1.48, 'H': 5.34, 'S': 4.39, 'O': 68.12}
			Density None, Hardness 2.5, Elements {'Ca': 16.46, 'Al': 0.37, 'Fe': 1.53, 'Si': 2.31, 'B': 1.48, 'H': 5.34, 'S': 4.39, 'O': 68.12}
	Found duplicates of "Bushmakinite", with these properties :
			Density None, Hardness 3.25, Elements {'Al': 3.11, 'V': 5.64, 'Zn': 0.1, 'Cr': 1.09, 'Cu': 2.0, 'P': 4.64, 'H': 0.15, 'Pb': 62.67, 'S': 0.05, 'O': 20.55}
			Density None, Hardness 3.25, Elements {'Al': 3.11, 'V': 5.64, 'Zn': 0.1, 'Cr': 1.09, 'Cu': 2.0, 'P': 4.64, 'H': 0.15, 'Pb': 62.67, 'S': 0.05, 'O': 20.55}
	Found duplicates of "Bussenite", with these properties :
			Density 3.63, Hardness 4.0, Elements {'Ba': 26.13, 'Na': 6.65, 'Sr': 5.33, 'Ca': 1.22, 'Ti': 7.29, 'Mn': 3.34, 'Fe': 5.1, 'Si': 8.55, 'H': 0.44, 'C': 1.65, 'O': 31.41, 'F': 2.89}
			Density 3.63, Hardness 4.0, Elements {'Ba': 26.13, 'Na': 6.65, 'Sr': 5.33, 'Ca': 1.22, 'Ti': 7.29, 'Mn': 3.34, 'Fe': 5.1, 'Si': 8.55, 'H': 0.44, 'C': 1.65, 'O': 31.41, 'F': 2.89}
	Found duplicates of "Bykovaite", with these properties :
			Density 2.98, Hardness 3.0, Elements {'K': 0.57, 'Ba': 11.83, 'Na': 9.87, 'Sr': 0.53, 'Ca': 0.15, 'Ti': 11.33, 'Mn': 1.73, 'Nb': 7.44, 'Al': 0.07, 'Fe': 0.2, 'Si': 13.56, 'H': 1.12, 'O': 40.2, 'F': 1.41}
			Density 2.98, Hardness 3.0, Elements {'K': 0.57, 'Ba': 11.83, 'Na': 9.87, 'Sr': 0.53, 'Ca': 0.15, 'Ti': 11.33, 'Mn': 1.73, 'Nb': 7.44, 'Al': 0.07, 'Fe': 0.2, 'Si': 13.56, 'H': 1.12, 'O': 40.2, 'F': 1.41}
	Found duplicates of "Bystromite", with these properties :
			Density 5.7, Hardness 7.0, Elements {'Mg': 6.68, 'Sb': 66.93, 'O': 26.39}
			Density 5.7, Hardness 7.0, Elements {'Mg': 6.68, 'Sb': 66.93, 'O': 26.39}
	Found duplicates of "Cabalzarite", with these properties :
			Density 3.89, Hardness 5.0, Elements {'Ca': 9.69, 'Mg': 4.7, 'Mn': 0.66, 'Al': 5.22, 'Fe': 4.05, 'As': 36.22, 'H': 0.79, 'O': 38.67}
			Density 3.89, Hardness 5.0, Elements {'Ca': 9.69, 'Mg': 4.7, 'Mn': 0.66, 'Al': 5.22, 'Fe': 4.05, 'As': 36.22, 'H': 0.79, 'O': 38.67}
	Found duplicates of "Cadmoindite", with these properties :
			Density None, Hardness None, Elements {'Zn': 0.71, 'Cd': 20.0, 'In': 49.58, 'Fe': 1.7, 'Ge': 0.32, 'S': 27.69}
			Density None, Hardness None, Elements {'Zn': 0.71, 'Cd': 20.0, 'In': 49.58, 'Fe': 1.7, 'Ge': 0.32, 'S': 27.69}
	Found duplicates of "Calcioancylite-Ce", with these properties :
			Density None, Hardness 4.25, Elements {'Ca': 11.96, 'Ce': 41.8, 'H': 0.9, 'C': 7.17, 'O': 38.18}
			Density None, Hardness 4.25, Elements {'Ca': 11.96, 'Ce': 41.8, 'H': 0.9, 'C': 7.17, 'O': 38.18}
	Found duplicates of "Calcioandyrobertsite-1M", with these properties :
			Density 4.011, Hardness 3.0, Elements {'K': 3.46, 'Ca': 3.55, 'Cu': 28.13, 'As': 33.16, 'H': 0.54, 'O': 31.16}
			Density 4.011, Hardness 3.0, Elements {'K': 3.46, 'Ca': 3.55, 'Cu': 28.13, 'As': 33.16, 'H': 0.54, 'O': 31.16}
			Density 4.011, Hardness 3.0, Elements {'K': 3.46, 'Ca': 3.55, 'Cu': 28.13, 'As': 33.16, 'H': 0.54, 'O': 31.16}
	Found duplicates of "Calcioandyrobertsite-2O", with these properties :
			Density 3.9, Hardness 3.5, Elements {'K': 3.46, 'Ca': 3.55, 'Cu': 28.13, 'As': 33.16, 'H': 0.54, 'O': 31.16}
			Density 3.9, Hardness 3.5, Elements {'K': 3.46, 'Ca': 3.55, 'Cu': 28.13, 'As': 33.16, 'H': 0.54, 'O': 31.16}
			Density 3.9, Hardness 3.5, Elements {'K': 3.46, 'Ca': 3.55, 'Cu': 28.13, 'As': 33.16, 'H': 0.54, 'O': 31.16}
	Found duplicates of "Calcioaravaipaite", with these properties :
			Density 4.85, Hardness 2.5, Elements {'Ca': 16.58, 'Al': 5.58, 'H': 0.21, 'Pb': 42.87, 'O': 3.31, 'F': 31.45}
			Density 4.85, Hardness 2.5, Elements {'Ca': 16.58, 'Al': 5.58, 'H': 0.21, 'Pb': 42.87, 'O': 3.31, 'F': 31.45}
	Found duplicates of "Calcioburbankite", with these properties :
			Density 3.45, Hardness 3.5, Elements {'Na': 11.55, 'Sr': 4.4, 'Ca': 12.08, 'RE': 21.71, 'C': 10.06, 'O': 40.2}
			Density 3.45, Hardness 3.5, Elements {'Na': 11.55, 'Sr': 4.4, 'Ca': 12.08, 'RE': 21.71, 'C': 10.06, 'O': 40.2}
	Found duplicates of "Calciocatapleiite", with these properties :
			Density 2.77, Hardness 4.75, Elements {'Ca': 9.21, 'Zr': 23.3, 'Si': 21.52, 'H': 1.03, 'O': 44.94}
			Density 2.77, Hardness 4.75, Elements {'Ca': 9.21, 'Zr': 23.3, 'Si': 21.52, 'H': 1.03, 'O': 44.94}
	Found duplicates of "Reinhardbraunsite", with these properties :
			Density 2.84, Hardness 5.5, Elements {'Ca': 47.76, 'Si': 13.39, 'H': 0.36, 'O': 36.23, 'F': 2.26}
			Density 2.84, Hardness 5.5, Elements {'Ca': 47.76, 'Si': 13.39, 'H': 0.36, 'O': 36.23, 'F': 2.26}
	Found duplicates of "Calciopetersite", with these properties :
			Density None, Hardness 3.0, Elements {'K': 0.09, 'Ca': 3.18, 'La': 0.61, 'Ce': 1.7, 'Pr': 0.16, 'Dy': 0.36, 'Y': 1.27, 'Yb': 0.19, 'Cu': 41.33, 'Si': 0.25, 'As': 1.82, 'P': 9.25, 'H': 1.41, 'Nd': 1.27, 'O': 37.11}
			Density None, Hardness 3.0, Elements {'K': 0.09, 'Ca': 3.18, 'La': 0.61, 'Ce': 1.7, 'Pr': 0.16, 'Dy': 0.36, 'Y': 1.27, 'Yb': 0.19, 'Cu': 41.33, 'Si': 0.25, 'As': 1.82, 'P': 9.25, 'H': 1.41, 'Nd': 1.27, 'O': 37.11}
	Found duplicates of "Calderonite", with these properties :
			Density None, Hardness 3.5, Elements {'V': 13.6, 'Fe': 7.06, 'Cu': 0.45, 'H': 0.21, 'Pb': 58.22, 'O': 20.46}
			Density None, Hardness 3.5, Elements {'V': 13.6, 'Fe': 7.06, 'Cu': 0.45, 'H': 0.21, 'Pb': 58.22, 'O': 20.46}
			Density None, Hardness 3.5, Elements {'V': 13.6, 'Fe': 7.06, 'Cu': 0.45, 'H': 0.21, 'Pb': 58.22, 'O': 20.46}
	Found duplicates of "Calvertite", with these properties :
			Density None, Hardness 4.5, Elements {'V': 0.05, 'Zn': 0.54, 'Ga': 0.35, 'Fe': 1.61, 'Cu': 61.38, 'Ge': 5.6, 'As': 1.45, 'S': 29.0}
			Density None, Hardness 4.5, Elements {'V': 0.05, 'Zn': 0.54, 'Ga': 0.35, 'Fe': 1.61, 'Cu': 61.38, 'Ge': 5.6, 'As': 1.45, 'S': 29.0}
	Found duplicates of "Pyromorphite", with these properties :
			Density 6.85, Hardness 3.75, Elements {'P': 6.85, 'Pb': 76.38, 'Cl': 2.61, 'O': 14.15}
			Density 6.85, Hardness 3.75, Elements {'P': 6.85, 'Pb': 76.38, 'Cl': 2.61, 'O': 14.15}
			Density 6.85, Hardness 3.75, Elements {'P': 6.85, 'Pb': 76.38, 'Cl': 2.61, 'O': 14.15}
	Found duplicates of "Cannonite", with these properties :
			Density 6.515, Hardness 4.0, Elements {'Bi': 74.1, 'H': 0.36, 'S': 5.69, 'O': 19.86}
			Density 6.515, Hardness 4.0, Elements {'Bi': 74.1, 'H': 0.36, 'S': 5.69, 'O': 19.86}
	Found duplicates of "Caoxite", with these properties :
			Density 1.85, Hardness 2.25, Elements {'Ca': 22.0, 'H': 3.32, 'C': 13.19, 'O': 61.49}
			Density 1.85, Hardness 2.25, Elements {'Ca': 22.0, 'H': 3.32, 'C': 13.19, 'O': 61.49}
	Found duplicates of "Piypite", with these properties :
			Density 3.1, Hardness 2.5, Elements {'K': 18.91, 'Cu': 30.74, 'S': 15.51, 'O': 34.83}
			Density 3.1, Hardness 2.5, Elements {'K': 18.91, 'Cu': 30.74, 'S': 15.51, 'O': 34.83}
	Found duplicates of "Carbokentbrooksite", with these properties :
			Density 3.14, Hardness 5.0, Elements {'K': 0.45, 'Na': 8.01, 'Sr': 1.26, 'Ca': 8.04, 'La': 1.6, 'Ce': 2.69, 'Pr': 0.23, 'Y': 0.37, 'Zr': 8.68, 'Ti': 0.26, 'Mn': 0.97, 'Nb': 2.62, 'Si': 22.59, 'H': 0.14, 'C': 0.23, 'Nd': 0.69, 'Cl': 0.31, 'O': 40.87}
			Density 3.14, Hardness 5.0, Elements {'K': 0.45, 'Na': 8.01, 'Sr': 1.26, 'Ca': 8.04, 'La': 1.6, 'Ce': 2.69, 'Pr': 0.23, 'Y': 0.37, 'Zr': 8.68, 'Ti': 0.26, 'Mn': 0.97, 'Nb': 2.62, 'Si': 22.59, 'H': 0.14, 'C': 0.23, 'Nd': 0.69, 'Cl': 0.31, 'O': 40.87}
	Found duplicates of "Carbonatecyanotrichite", with these properties :
			Density 2.66, Hardness 2.0, Elements {'Al': 8.7, 'Cu': 40.96, 'H': 2.6, 'C': 1.28, 'S': 1.76, 'O': 44.71}
			Density 2.66, Hardness 2.0, Elements {'Al': 8.7, 'Cu': 40.96, 'H': 2.6, 'C': 1.28, 'S': 1.76, 'O': 44.71}
	Found duplicates of "Carbonate-hydroxylapatite", with these properties :
			Density None, Hardness 5.0, Elements {'Ca': 41.33, 'P': 15.97, 'H': 0.21, 'C': 1.24, 'O': 41.25}
			Density None, Hardness 5.0, Elements {'Ca': 41.33, 'P': 15.97, 'H': 0.21, 'C': 1.24, 'O': 41.25}
			Density None, Hardness 5.0, Elements {'Ca': 41.33, 'P': 15.97, 'H': 0.21, 'C': 1.24, 'O': 41.25}
	Found duplicates of "Caresite", with these properties :
			Density 2.58, Hardness 2.0, Elements {'Al': 9.06, 'Fe': 37.51, 'H': 3.05, 'C': 2.02, 'O': 48.36}
			Density 2.58, Hardness 2.0, Elements {'Al': 9.06, 'Fe': 37.51, 'H': 3.05, 'C': 2.02, 'O': 48.36}
	Found duplicates of "Carlosruizite", with these properties :
			Density 3.42, Hardness 2.75, Elements {'K': 5.46, 'Na': 3.47, 'Mg': 5.48, 'Cr': 1.29, 'H': 0.54, 'Se': 11.74, 'S': 3.11, 'I': 34.31, 'O': 34.6}
			Density 3.42, Hardness 2.75, Elements {'K': 5.46, 'Na': 3.47, 'Mg': 5.48, 'Cr': 1.29, 'H': 0.54, 'Se': 11.74, 'S': 3.11, 'I': 34.31, 'O': 34.6}
	Found duplicates of "Carmichaelite", with these properties :
			Density None, Hardness 6.0, Elements {'Ti': 38.79, 'Cr': 12.91, 'Fe': 5.84, 'H': 0.66, 'O': 41.81}
			Density None, Hardness 6.0, Elements {'Ti': 38.79, 'Cr': 12.91, 'Fe': 5.84, 'H': 0.66, 'O': 41.81}
	Found duplicates of "Carraraite", with these properties :
			Density None, Hardness None, Elements {'Ca': 17.93, 'Ge': 10.83, 'H': 4.51, 'C': 1.61, 'S': 5.26, 'O': 59.87}
			Density None, Hardness None, Elements {'Ca': 17.93, 'Ge': 10.83, 'H': 4.51, 'C': 1.61, 'S': 5.26, 'O': 59.87}
	Found duplicates of "Caryochroite", with these properties :
			Density 2.99, Hardness 2.5, Elements {'K': 0.52, 'Na': 1.39, 'Sr': 2.76, 'Ca': 0.83, 'Mg': 1.42, 'Ti': 4.55, 'Mn': 2.34, 'Al': 0.36, 'Fe': 24.08, 'Si': 16.75, 'H': 1.04, 'O': 43.96}
			Density 2.99, Hardness 2.5, Elements {'K': 0.52, 'Na': 1.39, 'Sr': 2.76, 'Ca': 0.83, 'Mg': 1.42, 'Ti': 4.55, 'Mn': 2.34, 'Al': 0.36, 'Fe': 24.08, 'Si': 16.75, 'H': 1.04, 'O': 43.96}
	Found duplicates of "Cassagnaite", with these properties :
			Density None, Hardness None, Elements {'Ca': 13.28, 'Mg': 1.46, 'Mn': 11.58, 'Al': 4.06, 'V': 3.58, 'Fe': 8.97, 'Si': 14.66, 'H': 0.66, 'O': 41.75}
			Density None, Hardness None, Elements {'Ca': 13.28, 'Mg': 1.46, 'Mn': 11.58, 'Al': 4.06, 'V': 3.58, 'Fe': 8.97, 'Si': 14.66, 'H': 0.66, 'O': 41.75}
	Found duplicates of "Catalanoite", with these properties :
			Density None, Hardness 2.0, Elements {'Na': 16.48, 'P': 10.78, 'H': 5.94, 'O': 66.8}
			Density None, Hardness 2.0, Elements {'Na': 16.48, 'P': 10.78, 'H': 5.94, 'O': 66.8}
	Found duplicates of "Catamarcaite", with these properties :
			Density None, Hardness 3.5, Elements {'Fe': 0.19, 'Cu': 42.89, 'Ag': 0.12, 'Ge': 7.89, 'W': 20.99, 'S': 27.93}
			Density None, Hardness 3.5, Elements {'Fe': 0.19, 'Cu': 42.89, 'Ag': 0.12, 'Ge': 7.89, 'W': 20.99, 'S': 27.93}
	Found duplicates of "Ferrikatophorite", with these properties :
			Density 3.35, Hardness 5.0, Elements {'Na': 4.87, 'Ca': 4.25, 'Mg': 2.58, 'Al': 2.86, 'Fe': 23.68, 'Si': 20.84, 'H': 0.21, 'O': 40.71}
			Density 3.35, Hardness 5.0, Elements {'Na': 4.87, 'Ca': 4.25, 'Mg': 2.58, 'Al': 2.86, 'Fe': 23.68, 'Si': 20.84, 'H': 0.21, 'O': 40.71}
	Found duplicates of "Cattiite", with these properties :
			Density 1.65, Hardness 2.0, Elements {'Mg': 10.76, 'Fe': 0.08, 'P': 9.44, 'H': 6.74, 'O': 72.98}
			Density 1.65, Hardness 2.0, Elements {'Mg': 10.76, 'Fe': 0.08, 'P': 9.44, 'H': 6.74, 'O': 72.98}
	Found duplicates of "Pertlikite", with these properties :
			Density 2.59, Hardness None, Elements {'K': 4.0, 'Na': 0.01, 'Mg': 3.78, 'Mn': 0.77, 'Al': 1.4, 'Zn': 0.03, 'Fe': 13.63, 'H': 1.88, 'S': 19.9, 'O': 54.6}
			Density 2.59, Hardness None, Elements {'K': 4.0, 'Na': 0.01, 'Mg': 3.78, 'Mn': 0.77, 'Al': 1.4, 'Zn': 0.03, 'Fe': 13.63, 'H': 1.88, 'S': 19.9, 'O': 54.6}
	Found duplicates of "Pertsevite", with these properties :
			Density None, Hardness None, Elements {'Ca': 0.31, 'Mg': 35.11, 'Mn': 0.42, 'Fe': 2.99, 'Si': 3.86, 'B': 7.02, 'H': 0.19, 'O': 42.68, 'F': 7.41}
			Density None, Hardness None, Elements {'Ca': 0.31, 'Mg': 35.11, 'Mn': 0.42, 'Fe': 2.99, 'Si': 3.86, 'B': 7.02, 'H': 0.19, 'O': 42.68, 'F': 7.41}
	Found duplicates of "Peterbaylissite", with these properties :
			Density 7.14, Hardness 4.5, Elements {'Hg': 84.19, 'H': 0.71, 'C': 1.68, 'O': 13.43}
			Density 7.14, Hardness 4.5, Elements {'Hg': 84.19, 'H': 0.71, 'C': 1.68, 'O': 13.43}
	Found duplicates of "Petersenite-Ce", with these properties :
			Density 3.69, Hardness 3.0, Elements {'Na': 12.65, 'Sr': 1.3, 'Ca': 1.79, 'La': 12.4, 'Ce': 20.84, 'Pr': 2.1, 'C': 8.93, 'Nd': 4.29, 'O': 35.7}
			Density 3.69, Hardness 3.0, Elements {'Na': 12.65, 'Sr': 1.3, 'Ca': 1.79, 'La': 12.4, 'Ce': 20.84, 'Pr': 2.1, 'C': 8.93, 'Nd': 4.29, 'O': 35.7}
	Found duplicates of "Petewilliamsite", with these properties :
			Density None, Hardness 2.0, Elements {'Ca': 0.12, 'Fe': 0.03, 'Co': 14.21, 'Cu': 2.67, 'Ni': 15.01, 'As': 38.64, 'O': 29.32}
			Density None, Hardness 2.0, Elements {'Ca': 0.12, 'Fe': 0.03, 'Co': 14.21, 'Cu': 2.67, 'Ni': 15.01, 'As': 38.64, 'O': 29.32}
	Found duplicates of "Petitjeanite", with these properties :
			Density None, Hardness 4.5, Elements {'Bi': 73.77, 'P': 7.29, 'H': 0.12, 'O': 18.83}
			Density None, Hardness 4.5, Elements {'Bi': 73.77, 'P': 7.29, 'H': 0.12, 'O': 18.83}
	Found duplicates of "Petterdite", with these properties :
			Density None, Hardness 2.0, Elements {'Sr': 1.72, 'Al': 2.12, 'Cr': 15.29, 'H': 1.11, 'Pb': 40.63, 'C': 4.95, 'O': 34.19}
			Density None, Hardness 2.0, Elements {'Sr': 1.72, 'Al': 2.12, 'Cr': 15.29, 'H': 1.11, 'Pb': 40.63, 'C': 4.95, 'O': 34.19}
	Found duplicates of "Pezzottaite", with these properties :
			Density 2.97, Hardness 8.0, Elements {'Cs': 14.93, 'K': 0.06, 'Rb': 0.52, 'Na': 1.4, 'Li': 0.99, 'Be': 4.19, 'Al': 8.35, 'Si': 25.58, 'H': 0.03, 'O': 43.96}
			Density 2.97, Hardness 8.0, Elements {'Cs': 14.93, 'K': 0.06, 'Rb': 0.52, 'Na': 1.4, 'Li': 0.99, 'Be': 4.19, 'Al': 8.35, 'Si': 25.58, 'H': 0.03, 'O': 43.96}
	Found duplicates of "Ravatite", with these properties :
			Density 1.11, Hardness 1.0, Elements {'H': 5.66, 'C': 94.34}
			Density 1.11, Hardness 1.0, Elements {'H': 5.66, 'C': 94.34}
			Density 1.11, Hardness 1.0, Elements {'H': 5.66, 'C': 94.34}
	Found duplicates of "Philolithite", with these properties :
			Density 5.91, Hardness 3.5, Elements {'Mg': 1.35, 'Mn': 7.66, 'H': 0.34, 'Pb': 69.3, 'C': 1.34, 'S': 0.89, 'Cl': 3.95, 'O': 15.16}
			Density 5.91, Hardness 3.5, Elements {'Mg': 1.35, 'Mn': 7.66, 'H': 0.34, 'Pb': 69.3, 'C': 1.34, 'S': 0.89, 'Cl': 3.95, 'O': 15.16}
	Found duplicates of "Phosgenite", with these properties :
			Density 6.15, Hardness 2.75, Elements {'Pb': 75.99, 'C': 2.2, 'Cl': 13.0, 'O': 8.8}
			Density 6.15, Hardness 2.75, Elements {'Pb': 75.99, 'C': 2.2, 'Cl': 13.0, 'O': 8.8}
	Found duplicates of "Phosphoellenbergerite", with these properties :
			Density 3.0, Hardness 6.5, Elements {'Ca': 0.42, 'Mg': 33.62, 'Fe': 1.16, 'P': 16.43, 'H': 0.71, 'C': 0.75, 'O': 46.92}
			Density 3.0, Hardness 6.5, Elements {'Ca': 0.42, 'Mg': 33.62, 'Fe': 1.16, 'P': 16.43, 'H': 0.71, 'C': 0.75, 'O': 46.92}
	Found duplicates of "Phosphogartrellite", with these properties :
			Density 5.05, Hardness 4.5, Elements {'Fe': 10.13, 'Cu': 11.52, 'P': 11.23, 'H': 0.55, 'Pb': 37.57, 'O': 29.01}
			Density 5.05, Hardness 4.5, Elements {'Fe': 10.13, 'Cu': 11.52, 'P': 11.23, 'H': 0.55, 'Pb': 37.57, 'O': 29.01}
	Found duplicates of "Phosphoinnelite", with these properties :
			Density 3.82, Hardness 4.75, Elements {'K': 0.03, 'Ba': 37.88, 'Na': 4.57, 'Sr': 0.88, 'Ca': 0.12, 'Mg': 0.39, 'Ti': 10.3, 'Nb': 0.5, 'Al': 0.15, 'Fe': 1.12, 'Si': 8.48, 'P': 2.64, 'S': 2.14, 'O': 30.65, 'F': 0.15}
			Density 3.82, Hardness 4.75, Elements {'K': 0.03, 'Ba': 37.88, 'Na': 4.57, 'Sr': 0.88, 'Ca': 0.12, 'Mg': 0.39, 'Ti': 10.3, 'Nb': 0.5, 'Al': 0.15, 'Fe': 1.12, 'Si': 8.48, 'P': 2.64, 'S': 2.14, 'O': 30.65, 'F': 0.15}
	Found duplicates of "Phosphovanadylite", with these properties :
			Density 2.16, Hardness None, Elements {'K': 0.49, 'Ba': 6.9, 'Na': 0.29, 'Ca': 1.01, 'Al': 1.7, 'V': 21.77, 'P': 7.79, 'H': 3.76, 'O': 56.3}
			Density 2.16, Hardness None, Elements {'K': 0.49, 'Ba': 6.9, 'Na': 0.29, 'Ca': 1.01, 'Al': 1.7, 'V': 21.77, 'P': 7.79, 'H': 3.76, 'O': 56.3}
	Found duplicates of "Phosphowalpurgite", with these properties :
			Density None, Hardness 4.5, Elements {'Ca': 0.23, 'U': 15.56, 'V': 0.07, 'Fe': 0.28, 'Cu': 0.23, 'Si': 0.08, 'Bi': 58.71, 'As': 2.69, 'P': 3.34, 'H': 0.29, 'Pb': 0.15, 'O': 18.36}
			Density None, Hardness 4.5, Elements {'Ca': 0.23, 'U': 15.56, 'V': 0.07, 'Fe': 0.28, 'Cu': 0.23, 'Si': 0.08, 'Bi': 58.71, 'As': 2.69, 'P': 3.34, 'H': 0.29, 'Pb': 0.15, 'O': 18.36}
	Found duplicates of "Picromerite", with these properties :
			Density 2.028, Hardness 2.5, Elements {'K': 19.42, 'Mg': 6.04, 'H': 3.0, 'S': 15.92, 'O': 55.62}
			Density 2.028, Hardness 2.5, Elements {'K': 19.42, 'Mg': 6.04, 'H': 3.0, 'S': 15.92, 'O': 55.62}
	Found duplicates of "Piemontite", with these properties :
			Density 3.4, Hardness 6.5, Elements {'Ca': 16.42, 'Mn': 10.13, 'Al': 9.95, 'Fe': 3.43, 'Si': 17.26, 'H': 0.21, 'O': 42.61}
			Density 3.4, Hardness 6.5, Elements {'Ca': 16.42, 'Mn': 10.13, 'Al': 9.95, 'Fe': 3.43, 'Si': 17.26, 'H': 0.21, 'O': 42.61}
	Found duplicates of "Piergorite-Ce", with these properties :
			Density None, Hardness 5.75, Elements {'Li': 0.23, 'Ca': 22.41, 'La': 2.87, 'Ce': 5.42, 'Pr': 0.56, 'Sm': 0.1, 'Gd': 0.1, 'Y': 0.36, 'Th': 5.11, 'Mg': 0.03, 'Zr': 0.12, 'U': 0.64, 'Ti': 0.42, 'Mn': 0.26, 'Be': 0.22, 'Al': 0.79, 'Fe': 1.49, 'Si': 11.28, 'B': 5.77, 'H': 0.07, 'Nd': 1.35, 'Cl': 0.24, 'O': 38.96, 'F': 1.22}
			Density None, Hardness 5.75, Elements {'Li': 0.23, 'Ca': 22.41, 'La': 2.87, 'Ce': 5.42, 'Pr': 0.56, 'Sm': 0.1, 'Gd': 0.1, 'Y': 0.36, 'Th': 5.11, 'Mg': 0.03, 'Zr': 0.12, 'U': 0.64, 'Ti': 0.42, 'Mn': 0.26, 'Be': 0.22, 'Al': 0.79, 'Fe': 1.49, 'Si': 11.28, 'B': 5.77, 'H': 0.07, 'Nd': 1.35, 'Cl': 0.24, 'O': 38.96, 'F': 1.22}
	Found duplicates of "Pillaite", with these properties :
			Density None, Hardness 3.5, Elements {'Cu': 0.16, 'Sb': 30.52, 'Pb': 49.29, 'S': 18.86, 'Cl': 0.96, 'O': 0.2}
			Density None, Hardness 3.5, Elements {'Cu': 0.16, 'Sb': 30.52, 'Pb': 49.29, 'S': 18.86, 'Cl': 0.96, 'O': 0.2}
	Found duplicates of "Pingguite", with these properties :
			Density 8.53, Hardness 5.75, Elements {'Bi': 73.02, 'Te': 14.86, 'O': 12.11}
			Density 8.53, Hardness 5.75, Elements {'Bi': 73.02, 'Te': 14.86, 'O': 12.11}
	Found duplicates of "Piretite", with these properties :
			Density 4.0, Hardness 2.5, Elements {'Ca': 3.22, 'U': 57.39, 'H': 0.97, 'Se': 12.69, 'O': 25.72}
			Density 4.0, Hardness 2.5, Elements {'Ca': 3.22, 'U': 57.39, 'H': 0.97, 'Se': 12.69, 'O': 25.72}
	Found duplicates of "Epidote", with these properties :
			Density 3.45, Hardness 7.0, Elements {'Ca': 15.44, 'Al': 3.9, 'Fe': 24.2, 'Si': 16.22, 'H': 0.19, 'O': 40.05}
			Density 3.45, Hardness 7.0, Elements {'Ca': 15.44, 'Al': 3.9, 'Fe': 24.2, 'Si': 16.22, 'H': 0.19, 'O': 40.05}
	Found duplicates of "Pittongite", with these properties :
			Density None, Hardness 2.5, Elements {'K': 0.05, 'Na': 2.27, 'Ca': 0.36, 'Al': 0.24, 'Fe': 4.02, 'H': 0.53, 'W': 67.78, 'O': 24.74}
			Density None, Hardness 2.5, Elements {'K': 0.05, 'Na': 2.27, 'Ca': 0.36, 'Al': 0.24, 'Fe': 4.02, 'H': 0.53, 'W': 67.78, 'O': 24.74}
	Found duplicates of "Pizgrischite", with these properties :
			Density None, Hardness 3.5, Elements {'Fe': 0.76, 'Cu': 16.55, 'Bi': 60.95, 'Sb': 0.35, 'Pb': 2.12, 'Se': 0.04, 'S': 19.22}
			Density None, Hardness 3.5, Elements {'Fe': 0.76, 'Cu': 16.55, 'Bi': 60.95, 'Sb': 0.35, 'Pb': 2.12, 'Se': 0.04, 'S': 19.22}
	Found duplicates of "Platynite", with these properties :
			Density 7.98, Hardness 2.5, Elements {'Bi': 46.75, 'Pb': 23.17, 'Se': 26.49, 'S': 3.59}
			Density 7.98, Hardness 2.5, Elements {'Bi': 46.75, 'Pb': 23.17, 'Se': 26.49, 'S': 3.59}
	Found duplicates of "Plumboagardite", with these properties :
			Density None, Hardness 3.0, Elements {'Ca': 1.02, 'La': 1.65, 'Ce': 0.77, 'Pr': 0.13, 'Sm': 0.14, 'Gd': 0.14, 'Dy': 0.15, 'Y': 0.73, 'Fe': 1.07, 'Cu': 32.37, 'Si': 0.69, 'As': 19.39, 'P': 0.14, 'H': 1.16, 'Pb': 8.31, 'Nd': 1.05, 'O': 31.1}
			Density None, Hardness 3.0, Elements {'Ca': 1.02, 'La': 1.65, 'Ce': 0.77, 'Pr': 0.13, 'Sm': 0.14, 'Gd': 0.14, 'Dy': 0.15, 'Y': 0.73, 'Fe': 1.07, 'Cu': 32.37, 'Si': 0.69, 'As': 19.39, 'P': 0.14, 'H': 1.16, 'Pb': 8.31, 'Nd': 1.05, 'O': 31.1}
	Found duplicates of "Plumbotellurite", with these properties :
			Density 7.2, Hardness 2.0, Elements {'Te': 33.33, 'Pb': 54.13, 'O': 12.54}
			Density 7.2, Hardness 2.0, Elements {'Te': 33.33, 'Pb': 54.13, 'O': 12.54}
	Found duplicates of "Podlesnoite", with these properties :
			Density 3.62, Hardness 3.75, Elements {'Ba': 36.95, 'Na': 0.06, 'Sr': 0.12, 'Ca': 20.93, 'Fe': 0.29, 'C': 6.3, 'O': 25.32, 'F': 10.02}
			Density 3.62, Hardness 3.75, Elements {'Ba': 36.95, 'Na': 0.06, 'Sr': 0.12, 'Ca': 20.93, 'Fe': 0.29, 'C': 6.3, 'O': 25.32, 'F': 10.02}
	Found duplicates of "Poldervaartite", with these properties :
			Density 2.91, Hardness 5.0, Elements {'Ca': 30.41, 'Mn': 13.9, 'Si': 14.21, 'H': 1.02, 'O': 40.47}
			Density 2.91, Hardness 5.0, Elements {'Ca': 30.41, 'Mn': 13.9, 'Si': 14.21, 'H': 1.02, 'O': 40.47}
	Found duplicates of "Pyrolusite", with these properties :
			Density 4.73, Hardness 6.25, Elements {'Mn': 63.19, 'O': 36.81}
			Density 4.73, Hardness 6.25, Elements {'Mn': 63.19, 'O': 36.81}
	Found duplicates of "Polkanovite", with these properties :
			Density 10.22, Hardness None, Elements {'As': 29.81, 'Rh': 70.19}
			Density 10.22, Hardness None, Elements {'As': 29.81, 'Rh': 70.19}
	Found duplicates of "Polyakovite-Ce", with these properties :
			Density 4.75, Hardness 5.75, Elements {'Ca': 0.64, 'La': 13.28, 'Ce': 21.21, 'Pr': 2.24, 'Th': 1.85, 'Mg': 1.55, 'Ti': 5.72, 'Nb': 2.96, 'Cr': 4.97, 'Fe': 4.0, 'Si': 8.95, 'Nd': 4.6, 'O': 28.04}
			Density 4.75, Hardness 5.75, Elements {'Ca': 0.64, 'La': 13.28, 'Ce': 21.21, 'Pr': 2.24, 'Th': 1.85, 'Mg': 1.55, 'Ti': 5.72, 'Nb': 2.96, 'Cr': 4.97, 'Fe': 4.0, 'Si': 8.95, 'Nd': 4.6, 'O': 28.04}
	Found duplicates of "Polybasite", with these properties :
			Density 4.8, Hardness 2.75, Elements {'Cu': 8.85, 'Ag': 65.1, 'Sb': 7.07, 'As': 2.61, 'S': 16.38}
			Density 4.8, Hardness 2.75, Elements {'Cu': 8.85, 'Ag': 65.1, 'Sb': 7.07, 'As': 2.61, 'S': 16.38}
			Density 4.8, Hardness 2.75, Elements {'Cu': 8.85, 'Ag': 65.1, 'Sb': 7.07, 'As': 2.61, 'S': 16.38}
	Found duplicates of "Poppiite", with these properties :
			Density 3.36, Hardness None, Elements {'K': 0.06, 'Rb': 0.08, 'Na': 0.29, 'Ca': 14.46, 'Mg': 0.89, 'Ti': 0.09, 'Mn': 1.52, 'Al': 2.14, 'V': 19.5, 'Fe': 2.68, 'Cu': 0.12, 'Si': 15.42, 'H': 0.66, 'O': 42.09}
			Density 3.36, Hardness None, Elements {'K': 0.06, 'Rb': 0.08, 'Na': 0.29, 'Ca': 14.46, 'Mg': 0.89, 'Ti': 0.09, 'Mn': 1.52, 'Al': 2.14, 'V': 19.5, 'Fe': 2.68, 'Cu': 0.12, 'Si': 15.42, 'H': 0.66, 'O': 42.09}
	Found duplicates of "Potassiccarpholite", with these properties :
			Density 3.08, Hardness 5.0, Elements {'K': 3.33, 'Na': 0.39, 'Li': 0.63, 'Mn': 10.54, 'Al': 15.82, 'Fe': 1.11, 'Si': 17.45, 'H': 0.82, 'O': 42.31, 'F': 7.61}
			Density 3.08, Hardness 5.0, Elements {'K': 3.33, 'Na': 0.39, 'Li': 0.63, 'Mn': 10.54, 'Al': 15.82, 'Fe': 1.11, 'Si': 17.45, 'H': 0.82, 'O': 42.31, 'F': 7.61}
			Density 3.08, Hardness 5.0, Elements {'K': 3.33, 'Na': 0.39, 'Li': 0.63, 'Mn': 10.54, 'Al': 15.82, 'Fe': 1.11, 'Si': 17.45, 'H': 0.82, 'O': 42.31, 'F': 7.61}
	Found duplicates of "Potassic-chlorohastingsite", with these properties :
			Density 3.38, Hardness 6.0, Elements {'K': 2.36, 'Na': 0.92, 'Ca': 8.06, 'Mg': 2.44, 'Al': 5.42, 'Fe': 22.46, 'Si': 16.94, 'H': 0.06, 'Cl': 4.99, 'O': 36.35}
			Density 3.38, Hardness 6.0, Elements {'K': 2.36, 'Na': 0.92, 'Ca': 8.06, 'Mg': 2.44, 'Al': 5.42, 'Fe': 22.46, 'Si': 16.94, 'H': 0.06, 'Cl': 4.99, 'O': 36.35}
	Found duplicates of "Potassic-chloropargasite", with these properties :
			Density 3.29, Hardness 5.5, Elements {'K': 2.52, 'Na': 0.99, 'Ca': 8.18, 'Mg': 5.49, 'Al': 7.83, 'Fe': 13.21, 'Si': 18.11, 'H': 0.1, 'Cl': 4.19, 'O': 39.38}
			Density 3.29, Hardness 5.5, Elements {'K': 2.52, 'Na': 0.99, 'Ca': 8.18, 'Mg': 5.49, 'Al': 7.83, 'Fe': 13.21, 'Si': 18.11, 'H': 0.1, 'Cl': 4.19, 'O': 39.38}
			Density 3.29, Hardness 5.5, Elements {'K': 2.52, 'Na': 0.99, 'Ca': 8.18, 'Mg': 5.49, 'Al': 7.83, 'Fe': 13.21, 'Si': 18.11, 'H': 0.1, 'Cl': 4.19, 'O': 39.38}
			Density 3.29, Hardness 5.5, Elements {'K': 2.52, 'Na': 0.99, 'Ca': 8.18, 'Mg': 5.49, 'Al': 7.83, 'Fe': 13.21, 'Si': 18.11, 'H': 0.1, 'Cl': 4.19, 'O': 39.38}
	Found duplicates of "Potassic-magnesiohastingsite", with these properties :
			Density None, Hardness 6.5, Elements {'K': 2.24, 'Ba': 0.15, 'Na': 1.17, 'Ca': 8.23, 'Mg': 5.49, 'Ti': 0.98, 'Mn': 0.36, 'Al': 7.12, 'V': 0.06, 'Fe': 14.19, 'Si': 18.31, 'H': 0.21, 'O': 41.51}
			Density None, Hardness 6.5, Elements {'K': 2.24, 'Ba': 0.15, 'Na': 1.17, 'Ca': 8.23, 'Mg': 5.49, 'Ti': 0.98, 'Mn': 0.36, 'Al': 7.12, 'V': 0.06, 'Fe': 14.19, 'Si': 18.31, 'H': 0.21, 'O': 41.51}
	Found duplicates of "Potassicarfvedsonite", with these properties :
			Density None, Hardness 5.75, Elements {'K': 2.72, 'Na': 5.18, 'Li': 0.21, 'Ca': 0.21, 'Mg': 0.03, 'Ti': 0.25, 'Mn': 1.08, 'Al': 0.31, 'Zn': 0.14, 'Fe': 27.14, 'Si': 22.63, 'H': 0.19, 'O': 39.56, 'F': 0.36}
			Density None, Hardness 5.75, Elements {'K': 2.72, 'Na': 5.18, 'Li': 0.21, 'Ca': 0.21, 'Mg': 0.03, 'Ti': 0.25, 'Mn': 1.08, 'Al': 0.31, 'Zn': 0.14, 'Fe': 27.14, 'Si': 22.63, 'H': 0.19, 'O': 39.56, 'F': 0.36}
	Found duplicates of "Potassicferrisadanagaite", with these properties :
			Density 3.44, Hardness 5.75, Elements {'K': 2.55, 'Na': 1.43, 'Ca': 7.24, 'Mg': 1.43, 'Ti': 1.06, 'Mn': 1.5, 'Al': 9.86, 'Zn': 0.14, 'Fe': 18.72, 'Si': 15.46, 'H': 0.18, 'O': 39.84, 'F': 0.6}
			Density 3.44, Hardness 5.75, Elements {'K': 2.55, 'Na': 1.43, 'Ca': 7.24, 'Mg': 1.43, 'Ti': 1.06, 'Mn': 1.5, 'Al': 9.86, 'Zn': 0.14, 'Fe': 18.72, 'Si': 15.46, 'H': 0.18, 'O': 39.84, 'F': 0.6}
			Density 3.44, Hardness 5.75, Elements {'K': 2.55, 'Na': 1.43, 'Ca': 7.24, 'Mg': 1.43, 'Ti': 1.06, 'Mn': 1.5, 'Al': 9.86, 'Zn': 0.14, 'Fe': 18.72, 'Si': 15.46, 'H': 0.18, 'O': 39.84, 'F': 0.6}
	Found duplicates of "Potassicrichterite", with these properties :
			Density None, Hardness None, Elements {'K': 3.52, 'Na': 3.45, 'Ca': 4.82, 'Mg': 14.6, 'Si': 26.99, 'H': 0.12, 'O': 44.21, 'F': 2.28}
			Density None, Hardness None, Elements {'K': 3.52, 'Na': 3.45, 'Ca': 4.82, 'Mg': 14.6, 'Si': 26.99, 'H': 0.12, 'O': 44.21, 'F': 2.28}
	Found duplicates of "Potassicsadanagaite", with these properties :
			Density None, Hardness None, Elements {'K': 3.03, 'Na': 0.59, 'Ca': 8.29, 'Al': 9.07, 'Fe': 21.66, 'Si': 17.43, 'H': 0.21, 'O': 39.71}
			Density None, Hardness None, Elements {'K': 3.03, 'Na': 0.59, 'Ca': 8.29, 'Al': 9.07, 'Fe': 21.66, 'Si': 17.43, 'H': 0.21, 'O': 39.71}
	Found duplicates of "Pretulite", with these properties :
			Density 3.71, Hardness 5.0, Elements {'Sc': 32.13, 'P': 22.14, 'O': 45.74}
			Density 3.71, Hardness 5.0, Elements {'Sc': 32.13, 'P': 22.14, 'O': 45.74}
	Found duplicates of "Prewittite", with these properties :
			Density None, Hardness None, Elements {'K': 2.72, 'Zn': 4.55, 'Cu': 26.53, 'Pb': 21.63, 'Se': 10.99, 'Cl': 24.67, 'O': 8.91}
			Density None, Hardness None, Elements {'K': 2.72, 'Zn': 4.55, 'Cu': 26.53, 'Pb': 21.63, 'Se': 10.99, 'Cl': 24.67, 'O': 8.91}
	Found duplicates of "Samarskite-Y", with these properties :
			Density 5.69, Hardness 5.5, Elements {'RE': 14.41, 'Y': 5.93, 'U': 15.88, 'Ta': 12.07, 'Nb': 24.79, 'Fe': 5.59, 'O': 21.34}
			Density 5.69, Hardness 5.5, Elements {'RE': 14.41, 'Y': 5.93, 'U': 15.88, 'Ta': 12.07, 'Nb': 24.79, 'Fe': 5.59, 'O': 21.34}
	Found duplicates of "Pringleite", with these properties :
			Density 2.21, Hardness 3.5, Elements {'Ca': 18.31, 'B': 14.27, 'H': 2.56, 'Cl': 7.2, 'O': 57.66}
			Density 2.21, Hardness 3.5, Elements {'Ca': 18.31, 'B': 14.27, 'H': 2.56, 'Cl': 7.2, 'O': 57.66}
	Found duplicates of "Protoanthophyllite", with these properties :
			Density None, Hardness 6.0, Elements {'Na': 0.17, 'Mg': 19.13, 'Mn': 0.07, 'Al': 0.47, 'Fe': 4.25, 'Si': 27.68, 'Ni': 0.07, 'H': 0.25, 'O': 47.9}
			Density None, Hardness 6.0, Elements {'Na': 0.17, 'Mg': 19.13, 'Mn': 0.07, 'Al': 0.47, 'Fe': 4.25, 'Si': 27.68, 'Ni': 0.07, 'H': 0.25, 'O': 47.9}
	Found duplicates of "Pseudojohannite", with these properties :
			Density 4.31, Hardness None, Elements {'U': 52.08, 'Cu': 11.55, 'H': 1.57, 'S': 3.59, 'O': 31.21}
			Density 4.31, Hardness None, Elements {'U': 52.08, 'Cu': 11.55, 'H': 1.57, 'S': 3.59, 'O': 31.21}
	Found duplicates of "Pseudosinhalite", with these properties :
			Density 3.55, Hardness None, Elements {'Mg': 15.57, 'Al': 25.93, 'B': 6.93, 'H': 0.32, 'O': 51.25}
			Density 3.55, Hardness None, Elements {'Mg': 15.57, 'Al': 25.93, 'B': 6.93, 'H': 0.32, 'O': 51.25}
	Found duplicates of "Pumpellyite-Al", with these properties :
			Density None, Hardness 5.5, Elements {'Na': 0.05, 'Ca': 16.55, 'Mg': 1.21, 'Mn': 0.11, 'Al': 13.55, 'Fe': 3.82, 'Si': 17.48, 'H': 0.75, 'O': 46.48}
			Density None, Hardness 5.5, Elements {'Na': 0.05, 'Ca': 16.55, 'Mg': 1.21, 'Mn': 0.11, 'Al': 13.55, 'Fe': 3.82, 'Si': 17.48, 'H': 0.75, 'O': 46.48}
	Found duplicates of "Pumpellyite-Fe++", with these properties :
			Density 3.2, Hardness 5.5, Elements {'Ca': 16.52, 'Al': 11.12, 'Fe': 11.51, 'Si': 17.36, 'H': 0.62, 'O': 42.86}
			Density 3.2, Hardness 5.5, Elements {'Ca': 16.52, 'Al': 11.12, 'Fe': 11.51, 'Si': 17.36, 'H': 0.62, 'O': 42.86}
	Found duplicates of "Pumpellyite-Mg", with these properties :
			Density 3.2, Hardness 5.5, Elements {'Ca': 17.03, 'Mg': 5.16, 'Al': 11.46, 'Si': 17.9, 'H': 0.86, 'O': 47.59}
			Density 3.2, Hardness 5.5, Elements {'Ca': 17.03, 'Mg': 5.16, 'Al': 11.46, 'Si': 17.9, 'H': 0.86, 'O': 47.59}
	Found duplicates of "Pushcharovskite", with these properties :
			Density 3.35, Hardness None, Elements {'Cu': 27.57, 'As': 32.5, 'H': 1.75, 'O': 38.18}
			Density 3.35, Hardness None, Elements {'Cu': 27.57, 'As': 32.5, 'H': 1.75, 'O': 38.18}
	Found duplicates of "Putzite", with these properties :
			Density None, Hardness 3.25, Elements {'Cu': 32.81, 'Ag': 39.03, 'Ge': 7.76, 'S': 20.4}
			Density None, Hardness 3.25, Elements {'Cu': 32.81, 'Ag': 39.03, 'Ge': 7.76, 'S': 20.4}
	Found duplicates of "Pyatenkoite-Y", with these properties :
			Density 2.68, Hardness 4.5, Elements {'Na': 13.99, 'Gd': 1.91, 'Dy': 1.98, 'Y': 7.57, 'Ti': 5.83, 'Si': 20.51, 'H': 1.47, 'O': 46.73}
			Density 2.68, Hardness 4.5, Elements {'Na': 13.99, 'Gd': 1.91, 'Dy': 1.98, 'Y': 7.57, 'Ti': 5.83, 'Si': 20.51, 'H': 1.47, 'O': 46.73}
	Found duplicates of "Pyrargyrite", with these properties :
			Density 5.85, Hardness 2.5, Elements {'Ag': 59.75, 'Sb': 22.48, 'S': 17.76}
			Density 5.85, Hardness 2.5, Elements {'Ag': 59.75, 'Sb': 22.48, 'S': 17.76}
	Found duplicates of "Pyrope", with these properties :
			Density 3.74, Hardness 7.5, Elements {'Mg': 18.09, 'Al': 13.39, 'Si': 20.9, 'O': 47.63}
			Density 3.74, Hardness 7.5, Elements {'Mg': 18.09, 'Al': 13.39, 'Si': 20.9, 'O': 47.63}
	Found duplicates of "Pyrosmalite-Fe", with these properties :
			Density 3.12, Hardness 4.5, Elements {'Mg': 0.23, 'Mn': 3.65, 'Fe': 38.15, 'Si': 15.99, 'H': 0.9, 'Cl': 4.04, 'O': 37.04}
			Density 3.12, Hardness 4.5, Elements {'Mg': 0.23, 'Mn': 3.65, 'Fe': 38.15, 'Si': 15.99, 'H': 0.9, 'Cl': 4.04, 'O': 37.04}
	Found duplicates of "Qaqarssukite-Ce", with these properties :
			Density None, Hardness 3.5, Elements {'Ba': 24.6, 'Sr': 3.23, 'Ca': 2.01, 'La': 7.68, 'Ce': 17.35, 'Pr': 1.48, 'Sm': 0.4, 'Ti': 0.88, 'C': 6.33, 'Nd': 5.7, 'O': 25.29, 'F': 5.05}
			Density None, Hardness 3.5, Elements {'Ba': 24.6, 'Sr': 3.23, 'Ca': 2.01, 'La': 7.68, 'Ce': 17.35, 'Pr': 1.48, 'Sm': 0.4, 'Ti': 0.88, 'C': 6.33, 'Nd': 5.7, 'O': 25.29, 'F': 5.05}
	Found duplicates of "Qilianshanite", with these properties :
			Density 1.706, Hardness 2.0, Elements {'Na': 12.64, 'B': 5.94, 'H': 4.43, 'C': 6.6, 'O': 70.38}
			Density 1.706, Hardness 2.0, Elements {'Na': 12.64, 'B': 5.94, 'H': 4.43, 'C': 6.6, 'O': 70.38}
	Found duplicates of "Quadratite", with these properties :
			Density None, Hardness 3.0, Elements {'Cd': 21.91, 'Ag': 26.29, 'As': 18.26, 'Pb': 10.1, 'S': 23.44}
			Density None, Hardness 3.0, Elements {'Cd': 21.91, 'Ag': 26.29, 'As': 18.26, 'Pb': 10.1, 'S': 23.44}
	Found duplicates of "Quintinite-2H", with these properties :
			Density 2.14, Hardness 2.0, Elements {'Mg': 19.95, 'Al': 11.07, 'H': 4.14, 'C': 2.46, 'O': 62.38}
			Density 2.14, Hardness 2.0, Elements {'Mg': 19.95, 'Al': 11.07, 'H': 4.14, 'C': 2.46, 'O': 62.38}
	Found duplicates of "Quintinite-3T", with these properties :
			Density 2.14, Hardness 2.0, Elements {'Mg': 19.95, 'Al': 11.07, 'H': 4.14, 'C': 2.46, 'O': 62.38}
			Density 2.14, Hardness 2.0, Elements {'Mg': 19.95, 'Al': 11.07, 'H': 4.14, 'C': 2.46, 'O': 62.38}
	Found duplicates of "Raadeite", with these properties :
			Density None, Hardness None, Elements {'Mg': 33.38, 'Mn': 0.22, 'Fe': 0.23, 'As': 0.3, 'P': 12.3, 'H': 1.72, 'O': 51.85}
			Density None, Hardness None, Elements {'Mg': 33.38, 'Mn': 0.22, 'Fe': 0.23, 'As': 0.3, 'P': 12.3, 'H': 1.72, 'O': 51.85}
	Found duplicates of "Rabejacite", with these properties :
			Density 4.1, Hardness 3.0, Elements {'Ca': 2.63, 'U': 62.54, 'H': 1.19, 'S': 4.21, 'O': 29.43}
			Density 4.1, Hardness 3.0, Elements {'Ca': 2.63, 'U': 62.54, 'H': 1.19, 'S': 4.21, 'O': 29.43}
	Found duplicates of "Radovanite", with these properties :
			Density 3.9, Hardness None, Elements {'Fe': 8.73, 'Cu': 20.96, 'As': 39.03, 'H': 0.73, 'O': 30.56}
			Density 3.9, Hardness None, Elements {'Fe': 8.73, 'Cu': 20.96, 'As': 39.03, 'H': 0.73, 'O': 30.56}
	Found duplicates of "Ramanite-Cs", with these properties :
			Density None, Hardness None, Elements {'Cs': 34.34, 'B': 13.97, 'H': 2.08, 'O': 49.61}
			Density None, Hardness None, Elements {'Cs': 34.34, 'B': 13.97, 'H': 2.08, 'O': 49.61}
	Found duplicates of "Ramanite-Rb", with these properties :
			Density None, Hardness None, Elements {'Rb': 25.17, 'B': 15.92, 'H': 2.37, 'O': 56.54}
			Density None, Hardness None, Elements {'Rb': 25.17, 'B': 15.92, 'H': 2.37, 'O': 56.54}
	Found duplicates of "Rambergite", with these properties :
			Density None, Hardness 4.0, Elements {'Mn': 63.14, 'S': 36.86}
			Density None, Hardness 4.0, Elements {'Mn': 63.14, 'S': 36.86}
	Found duplicates of "Rappoldite", with these properties :
			Density None, Hardness 4.5, Elements {'Zn': 4.08, 'Co': 9.19, 'Ni': 5.49, 'As': 23.36, 'H': 0.63, 'Pb': 32.31, 'O': 24.95}
			Density None, Hardness 4.5, Elements {'Zn': 4.08, 'Co': 9.19, 'Ni': 5.49, 'As': 23.36, 'H': 0.63, 'Pb': 32.31, 'O': 24.95}
	Found duplicates of "Raslakite", with these properties :
			Density 2.95, Hardness 5.0, Elements {'Na': 15.03, 'Ca': 4.48, 'Zr': 2.47, 'Nb': 0.84, 'Fe': 6.04, 'Si': 26.07, 'H': 0.15, 'Cl': 0.96, 'O': 43.98}
			Density 2.95, Hardness 5.0, Elements {'Na': 15.03, 'Ca': 4.48, 'Zr': 2.47, 'Nb': 0.84, 'Fe': 6.04, 'Si': 26.07, 'H': 0.15, 'Cl': 0.96, 'O': 43.98}
	Found duplicates of "Rastsvetaevite", with these properties :
			Density None, Hardness None, Elements {'K': 5.16, 'Na': 10.24, 'Ca': 7.93, 'Zr': 9.03, 'Fe': 2.76, 'Si': 24.09, 'H': 0.05, 'Cl': 1.17, 'O': 39.58}
			Density None, Hardness None, Elements {'K': 5.16, 'Na': 10.24, 'Ca': 7.93, 'Zr': 9.03, 'Fe': 2.76, 'Si': 24.09, 'H': 0.05, 'Cl': 1.17, 'O': 39.58}
	Found duplicates of "Dufrenoysite", with these properties :
			Density 5.56, Hardness 3.0, Elements {'As': 20.68, 'Pb': 57.19, 'S': 22.13}
			Density 5.56, Hardness 3.0, Elements {'As': 20.68, 'Pb': 57.19, 'S': 22.13}
	Found duplicates of "Sartorite", with these properties :
			Density 5.1, Hardness 3.0, Elements {'Tl': 6.39, 'Sb': 1.36, 'As': 29.27, 'Pb': 37.93, 'S': 25.05}
			Density 5.1, Hardness 3.0, Elements {'Tl': 6.39, 'Sb': 1.36, 'As': 29.27, 'Pb': 37.93, 'S': 25.05}
			Density 5.1, Hardness 3.0, Elements {'Tl': 6.39, 'Sb': 1.36, 'As': 29.27, 'Pb': 37.93, 'S': 25.05}
			Density 5.1, Hardness 3.0, Elements {'Tl': 6.39, 'Sb': 1.36, 'As': 29.27, 'Pb': 37.93, 'S': 25.05}
	Found duplicates of "Redgillite", with these properties :
			Density 3.45, Hardness 2.0, Elements {'Cu': 57.27, 'H': 1.82, 'S': 4.83, 'O': 36.08}
			Density 3.45, Hardness 2.0, Elements {'Cu': 57.27, 'H': 1.82, 'S': 4.83, 'O': 36.08}
	Found duplicates of "Reederite-Y", with these properties :
			Density 2.91, Hardness 3.25, Elements {'Na': 28.8, 'Y': 14.85, 'C': 9.03, 'S': 2.68, 'Cl': 2.96, 'O': 40.09, 'F': 1.59}
			Density 2.91, Hardness 3.25, Elements {'Na': 28.8, 'Y': 14.85, 'C': 9.03, 'S': 2.68, 'Cl': 2.96, 'O': 40.09, 'F': 1.59}
	Found duplicates of "Epsomite", with these properties :
			Density 1.67, Hardness 2.25, Elements {'Mg': 9.86, 'H': 5.73, 'S': 13.01, 'O': 71.4}
			Density 1.67, Hardness 2.25, Elements {'Mg': 9.86, 'H': 5.73, 'S': 13.01, 'O': 71.4}
			Density 1.67, Hardness 2.25, Elements {'Mg': 9.86, 'H': 5.73, 'S': 13.01, 'O': 71.4}
	Found duplicates of "Reidite", with these properties :
			Density None, Hardness 7.5, Elements {'Zr': 49.77, 'Si': 15.32, 'O': 34.91}
			Density None, Hardness 7.5, Elements {'Zr': 49.77, 'Si': 15.32, 'O': 34.91}
	Found duplicates of "Remondite-La", with these properties :
			Density 3.5, Hardness 3.0, Elements {'Na': 12.25, 'Sr': 2.8, 'Ca': 3.91, 'La': 17.78, 'Ce': 15.25, 'C': 9.61, 'O': 38.4}
			Density 3.5, Hardness 3.0, Elements {'Na': 12.25, 'Sr': 2.8, 'Ca': 3.91, 'La': 17.78, 'Ce': 15.25, 'C': 9.61, 'O': 38.4}
	Found duplicates of "Rengeite", with these properties :
			Density None, Hardness 5.25, Elements {'Sr': 29.67, 'Ca': 0.38, 'RE': 0.68, 'Zr': 6.86, 'Ti': 18.47, 'Si': 10.83, 'O': 33.11}
			Density None, Hardness 5.25, Elements {'Sr': 29.67, 'Ca': 0.38, 'RE': 0.68, 'Zr': 6.86, 'Ti': 18.47, 'Si': 10.83, 'O': 33.11}
	Found duplicates of "Retzian-Nd", with these properties :
			Density None, Hardness None, Elements {'La': 3.02, 'Ce': 9.15, 'Mn': 23.92, 'As': 16.31, 'H': 0.88, 'Nd': 18.84, 'O': 27.87}
			Density None, Hardness None, Elements {'La': 3.02, 'Ce': 9.15, 'Mn': 23.92, 'As': 16.31, 'H': 0.88, 'Nd': 18.84, 'O': 27.87}
	Found duplicates of "Schapbachite", with these properties :
			Density None, Hardness 2.5, Elements {'Ag': 22.0, 'Bi': 43.16, 'Pb': 18.49, 'S': 16.35}
			Density None, Hardness 2.5, Elements {'Ag': 22.0, 'Bi': 43.16, 'Pb': 18.49, 'S': 16.35}
	Found duplicates of "Schreibersite", with these properties :
			Density 7.4, Hardness 6.75, Elements {'Fe': 62.63, 'Ni': 21.94, 'P': 15.44}
			Density 7.4, Hardness 6.75, Elements {'Fe': 62.63, 'Ni': 21.94, 'P': 15.44}
	Found duplicates of "Rheniite", with these properties :
			Density None, Hardness None, Elements {'Re': 74.38, 'S': 25.62}
			Density None, Hardness None, Elements {'Re': 74.38, 'S': 25.62}
	Found duplicates of "Rhodarsenide", with these properties :
			Density 11.29, Hardness 4.5, Elements {'As': 26.52, 'Pd': 18.84, 'Rh': 54.64}
			Density 11.29, Hardness 4.5, Elements {'As': 26.52, 'Pd': 18.84, 'Rh': 54.64}
	Found duplicates of "Rhonite", with these properties :
			Density 3.58, Hardness 5.5, Elements {'K': 0.48, 'Na': 0.56, 'Ca': 8.86, 'Mg': 7.46, 'Ti': 5.88, 'Al': 8.94, 'Fe': 17.14, 'Si': 11.38, 'O': 39.29}
			Density 3.58, Hardness 5.5, Elements {'K': 0.48, 'Na': 0.56, 'Ca': 8.86, 'Mg': 7.46, 'Ti': 5.88, 'Al': 8.94, 'Fe': 17.14, 'Si': 11.38, 'O': 39.29}
	Found duplicates of "Riebeckite", with these properties :
			Density 3.4, Hardness 4.0, Elements {'Na': 4.91, 'Fe': 29.84, 'Si': 24.01, 'H': 0.22, 'O': 41.03}
			Density 3.4, Hardness 4.0, Elements {'Na': 4.91, 'Fe': 29.84, 'Si': 24.01, 'H': 0.22, 'O': 41.03}
	Found duplicates of "Rinkite", with these properties :
			Density 3.5, Hardness 5.0, Elements {'Na': 7.01, 'Ca': 19.54, 'Ce': 17.08, 'Ti': 4.38, 'Nb': 2.83, 'Si': 13.69, 'O': 33.15, 'F': 2.32}
			Density 3.5, Hardness 5.0, Elements {'Na': 7.01, 'Ca': 19.54, 'Ce': 17.08, 'Ti': 4.38, 'Nb': 2.83, 'Si': 13.69, 'O': 33.15, 'F': 2.32}
	Found duplicates of "Rinmanite", with these properties :
			Density None, Hardness 6.5, Elements {'Mg': 5.43, 'Mn': 1.84, 'Al': 0.6, 'Zn': 11.7, 'Fe': 24.35, 'Sb': 27.22, 'H': 0.23, 'O': 28.62}
			Density None, Hardness 6.5, Elements {'Mg': 5.43, 'Mn': 1.84, 'Al': 0.6, 'Zn': 11.7, 'Fe': 24.35, 'Sb': 27.22, 'H': 0.23, 'O': 28.62}
	Found duplicates of "Riomarinaite", with these properties :
			Density None, Hardness 2.5, Elements {'Bi': 61.7, 'H': 0.77, 'S': 9.75, 'O': 27.79}
			Density None, Hardness 2.5, Elements {'Bi': 61.7, 'H': 0.77, 'S': 9.75, 'O': 27.79}
	Found duplicates of "Rodolicoite", with these properties :
			Density 3.07, Hardness None, Elements {'Fe': 37.03, 'P': 20.54, 'O': 42.43}
			Density 3.07, Hardness None, Elements {'Fe': 37.03, 'P': 20.54, 'O': 42.43}
	Found duplicates of "Rokuhnite", with these properties :
			Density 2.35, Hardness None, Elements {'Fe': 38.58, 'H': 1.39, 'Cl': 48.98, 'O': 11.05}
			Density 2.35, Hardness None, Elements {'Fe': 38.58, 'H': 1.39, 'Cl': 48.98, 'O': 11.05}
	Found duplicates of "Rollandite", with these properties :
			Density 3.9, Hardness 4.25, Elements {'Cu': 36.26, 'As': 27.58, 'H': 1.41, 'O': 34.75}
			Density 3.9, Hardness 4.25, Elements {'Cu': 36.26, 'As': 27.58, 'H': 1.41, 'O': 34.75}
	Found duplicates of "Rondorfite", with these properties :
			Density None, Hardness None, Elements {'Na': 0.06, 'Ca': 41.41, 'Mg': 2.76, 'Ti': 0.06, 'Al': 0.21, 'Fe': 0.43, 'Si': 14.49, 'H': 0.04, 'Cl': 6.84, 'O': 33.7}
			Density None, Hardness None, Elements {'Na': 0.06, 'Ca': 41.41, 'Mg': 2.76, 'Ti': 0.06, 'Al': 0.21, 'Fe': 0.43, 'Si': 14.49, 'H': 0.04, 'Cl': 6.84, 'O': 33.7}
	Found duplicates of "Ronneburgite", with these properties :
			Density 2.84, Hardness 3.0, Elements {'K': 14.23, 'Mg': 0.47, 'Mn': 9.47, 'V': 39.04, 'O': 36.79}
			Density 2.84, Hardness 3.0, Elements {'K': 14.23, 'Mg': 0.47, 'Mn': 9.47, 'V': 39.04, 'O': 36.79}
	Found duplicates of "Rosenbergite", with these properties :
			Density 2.1, Hardness 3.25, Elements {'Al': 17.18, 'H': 3.85, 'O': 30.57, 'F': 48.4}
			Density 2.1, Hardness 3.25, Elements {'Al': 17.18, 'H': 3.85, 'O': 30.57, 'F': 48.4}
	Found duplicates of "Rosiaite", with these properties :
			Density 6.96, Hardness 5.5, Elements {'Sb': 44.54, 'Pb': 37.9, 'O': 17.56}
			Density 6.96, Hardness 5.5, Elements {'Sb': 44.54, 'Pb': 37.9, 'O': 17.56}
	Found duplicates of "Rossmanite", with these properties :
			Density 3.0, Hardness 7.0, Elements {'Li': 0.75, 'Al': 23.37, 'Si': 18.24, 'B': 3.51, 'H': 0.44, 'O': 53.69}
			Density 3.0, Hardness 7.0, Elements {'Li': 0.75, 'Al': 23.37, 'Si': 18.24, 'B': 3.51, 'H': 0.44, 'O': 53.69}
	Found duplicates of "Rouaite", with these properties :
			Density 3.38, Hardness None, Elements {'Cu': 52.93, 'H': 1.26, 'N': 5.83, 'O': 39.98}
			Density 3.38, Hardness None, Elements {'Cu': 52.93, 'H': 1.26, 'N': 5.83, 'O': 39.98}
	Found duplicates of "Rouxelite", with these properties :
			Density None, Hardness None, Elements {'Cu': 1.33, 'Hg': 1.76, 'Sb': 31.47, 'Pb': 45.01, 'S': 20.04, 'O': 0.39}
			Density None, Hardness None, Elements {'Cu': 1.33, 'Hg': 1.76, 'Sb': 31.47, 'Pb': 45.01, 'S': 20.04, 'O': 0.39}
	Found duplicates of "Rubicline", with these properties :
			Density None, Hardness None, Elements {'K': 3.12, 'Rb': 20.47, 'Al': 8.62, 'Si': 26.91, 'O': 40.88}
			Density None, Hardness None, Elements {'K': 3.12, 'Rb': 20.47, 'Al': 8.62, 'Si': 26.91, 'O': 40.88}
	Found duplicates of "Rudashevskyite", with these properties :
			Density None, Hardness 4.0, Elements {'Mn': 2.39, 'Zn': 24.92, 'Fe': 37.09, 'Cu': 0.69, 'S': 34.91}
			Density None, Hardness 4.0, Elements {'Mn': 2.39, 'Zn': 24.92, 'Fe': 37.09, 'Cu': 0.69, 'S': 34.91}
	Found duplicates of "Rudenkoite", with these properties :
			Density None, Hardness 1.5, Elements {'Ba': 5.17, 'Sr': 28.17, 'Ca': 0.59, 'Al': 10.74, 'Si': 11.14, 'H': 1.09, 'Cl': 8.73, 'O': 34.37}
			Density None, Hardness 1.5, Elements {'Ba': 5.17, 'Sr': 28.17, 'Ca': 0.59, 'Al': 10.74, 'Si': 11.14, 'H': 1.09, 'Cl': 8.73, 'O': 34.37}
	Found duplicates of "Ruifrancoite", with these properties :
			Density 2.88, Hardness 4.5, Elements {'Ca': 7.04, 'Mg': 1.94, 'Mn': 6.28, 'Be': 3.35, 'Al': 0.45, 'Fe': 11.84, 'P': 17.27, 'H': 1.41, 'O': 50.41}
			Density 2.88, Hardness 4.5, Elements {'Ca': 7.04, 'Mg': 1.94, 'Mn': 6.28, 'Be': 3.35, 'Al': 0.45, 'Fe': 11.84, 'P': 17.27, 'H': 1.41, 'O': 50.41}
	Found duplicates of "Ruitenbergite", with these properties :
			Density 2.21, Hardness 3.5, Elements {'Ca': 18.31, 'B': 14.27, 'H': 2.56, 'Cl': 7.2, 'O': 57.66}
			Density 2.21, Hardness 3.5, Elements {'Ca': 18.31, 'B': 14.27, 'H': 2.56, 'Cl': 7.2, 'O': 57.66}
	Found duplicates of "Rutile", with these properties :
			Density 4.25, Hardness 6.25, Elements {'Ti': 59.94, 'O': 40.06}
			Density 4.25, Hardness 6.25, Elements {'Ti': 59.94, 'O': 40.06}
	Found duplicates of "Sabelliite", with these properties :
			Density 4.65, Hardness 4.5, Elements {'Zn': 16.59, 'Cu': 32.25, 'Sb': 7.72, 'As': 14.26, 'H': 0.77, 'O': 28.42}
			Density 4.65, Hardness 4.5, Elements {'Zn': 16.59, 'Cu': 32.25, 'Sb': 7.72, 'As': 14.26, 'H': 0.77, 'O': 28.42}
	Found duplicates of "Saddlebackite", with these properties :
			Density None, Hardness 2.25, Elements {'Bi': 35.31, 'Te': 21.56, 'Pb': 35.01, 'S': 8.13}
			Density None, Hardness 2.25, Elements {'Bi': 35.31, 'Te': 21.56, 'Pb': 35.01, 'S': 8.13}
	Found duplicates of "Sailaufite", with these properties :
			Density None, Hardness 3.5, Elements {'Na': 1.41, 'Ca': 8.63, 'Mn': 23.65, 'As': 24.19, 'H': 0.93, 'C': 1.85, 'O': 39.35}
			Density None, Hardness 3.5, Elements {'Na': 1.41, 'Ca': 8.63, 'Mn': 23.65, 'As': 24.19, 'H': 0.93, 'C': 1.85, 'O': 39.35}
	Found duplicates of "Salammoniac", with these properties :
			Density 1.5, Hardness 1.75, Elements {'H': 7.54, 'N': 26.19, 'Cl': 66.28}
			Density 1.5, Hardness 1.75, Elements {'H': 7.54, 'N': 26.19, 'Cl': 66.28}
			Density 1.5, Hardness 1.75, Elements {'H': 7.54, 'N': 26.19, 'Cl': 66.28}
	Found duplicates of "Salzburgite", with these properties :
			Density None, Hardness None, Elements {'Fe': 0.08, 'Cu': 4.64, 'Bi': 61.67, 'Pb': 15.81, 'S': 17.8}
			Density None, Hardness None, Elements {'Fe': 0.08, 'Cu': 4.64, 'Bi': 61.67, 'Pb': 15.81, 'S': 17.8}
	Found duplicates of "Samarskite-Yb", with these properties :
			Density 7.03, Hardness 5.5, Elements {'Ca': 1.58, 'La': 0.04, 'Ce': 0.18, 'Pr': 0.05, 'Sm': 0.39, 'Gd': 0.5, 'Dy': 2.81, 'Y': 1.59, 'Ho': 0.53, 'Er': 3.11, 'Tm': 0.54, 'Lu': 0.9, 'Tb': 0.25, 'Th': 9.29, 'Yb': 5.32, 'Zr': 0.76, 'Sc': 0.17, 'U': 9.6, 'Ta': 12.98, 'Ti': 0.41, 'Mn': 0.83, 'Nb': 21.66, 'Fe': 2.07, 'Si': 0.2, 'Sn': 0.46, 'Pb': 0.27, 'W': 2.47, 'Nd': 0.55, 'O': 20.49}
			Density 7.03, Hardness 5.5, Elements {'Ca': 1.58, 'La': 0.04, 'Ce': 0.18, 'Pr': 0.05, 'Sm': 0.39, 'Gd': 0.5, 'Dy': 2.81, 'Y': 1.59, 'Ho': 0.53, 'Er': 3.11, 'Tm': 0.54, 'Lu': 0.9, 'Tb': 0.25, 'Th': 9.29, 'Yb': 5.32, 'Zr': 0.76, 'Sc': 0.17, 'U': 9.6, 'Ta': 12.98, 'Ti': 0.41, 'Mn': 0.83, 'Nb': 21.66, 'Fe': 2.07, 'Si': 0.2, 'Sn': 0.46, 'Pb': 0.27, 'W': 2.47, 'Nd': 0.55, 'O': 20.49}
	Found duplicates of "Samfowlerite", with these properties :
			Density 3.28, Hardness 2.75, Elements {'Ca': 24.7, 'Mn': 7.26, 'Be': 2.02, 'Zn': 7.48, 'Si': 17.31, 'H': 0.43, 'O': 39.8, 'F': 1.0}
			Density 3.28, Hardness 2.75, Elements {'Ca': 24.7, 'Mn': 7.26, 'Be': 2.02, 'Zn': 7.48, 'Si': 17.31, 'H': 0.43, 'O': 39.8, 'F': 1.0}
	Found duplicates of "Fettelite", with these properties :
			Density 6.29, Hardness 3.75, Elements {'Tl': 0.08, 'Fe': 0.02, 'Cu': 0.09, 'Ag': 63.12, 'Hg': 7.22, 'Sb': 1.57, 'As': 10.05, 'Pb': 0.15, 'S': 17.7}
			Density 6.29, Hardness 3.75, Elements {'Tl': 0.08, 'Fe': 0.02, 'Cu': 0.09, 'Ag': 63.12, 'Hg': 7.22, 'Sb': 1.57, 'As': 10.05, 'Pb': 0.15, 'S': 17.7}
			Density 6.29, Hardness 3.75, Elements {'Tl': 0.08, 'Fe': 0.02, 'Cu': 0.09, 'Ag': 63.12, 'Hg': 7.22, 'Sb': 1.57, 'As': 10.05, 'Pb': 0.15, 'S': 17.7}
	Found duplicates of "Sanromanite", with these properties :
			Density None, Hardness None, Elements {'Na': 4.56, 'Ca': 3.98, 'Pb': 61.68, 'C': 5.96, 'O': 23.82}
			Density None, Hardness None, Elements {'Na': 4.56, 'Ca': 3.98, 'Pb': 61.68, 'C': 5.96, 'O': 23.82}
	Found duplicates of "Santarosaite", with these properties :
			Density None, Hardness None, Elements {'Ca': 1.34, 'Cu': 36.67, 'B': 14.87, 'Pb': 4.17, 'O': 42.94}
			Density None, Hardness None, Elements {'Ca': 1.34, 'Cu': 36.67, 'B': 14.87, 'Pb': 4.17, 'O': 42.94}
	Found duplicates of "Sazhinite-La", with these properties :
			Density None, Hardness 3.0, Elements {'K': 0.12, 'Na': 10.11, 'Sr': 0.13, 'Li': 0.01, 'Ca': 0.49, 'La': 8.72, 'Ce': 7.51, 'Pr': 0.43, 'Y': 0.14, 'Th': 3.2, 'Zr': 0.14, 'U': 0.36, 'Si': 25.25, 'B': 0.02, 'H': 0.62, 'S': 0.29, 'Nd': 0.88, 'O': 41.17, 'F': 0.41}
			Density None, Hardness 3.0, Elements {'K': 0.12, 'Na': 10.11, 'Sr': 0.13, 'Li': 0.01, 'Ca': 0.49, 'La': 8.72, 'Ce': 7.51, 'Pr': 0.43, 'Y': 0.14, 'Th': 3.2, 'Zr': 0.14, 'U': 0.36, 'Si': 25.25, 'B': 0.02, 'H': 0.62, 'S': 0.29, 'Nd': 0.88, 'O': 41.17, 'F': 0.41}
	Found duplicates of "Sazykinaite-Y", with these properties :
			Density 2.69, Hardness 5.0, Elements {'Na': 13.37, 'Y': 10.34, 'Zr': 10.61, 'Si': 19.6, 'H': 1.41, 'O': 44.67}
			Density 2.69, Hardness 5.0, Elements {'Na': 13.37, 'Y': 10.34, 'Zr': 10.61, 'Si': 19.6, 'H': 1.41, 'O': 44.67}
	Found duplicates of "Scainiite", with these properties :
			Density None, Hardness None, Elements {'Sb': 41.9, 'Pb': 36.98, 'S': 20.46, 'O': 0.65}
			Density None, Hardness None, Elements {'Sb': 41.9, 'Pb': 36.98, 'S': 20.46, 'O': 0.65}
	Found duplicates of "Scandiobabingtonite", with these properties :
			Density 3.24, Hardness 6.0, Elements {'Ca': 14.26, 'Sc': 8.0, 'Mn': 2.44, 'Fe': 7.45, 'Si': 24.98, 'H': 0.18, 'O': 42.69}
			Density 3.24, Hardness 6.0, Elements {'Ca': 14.26, 'Sc': 8.0, 'Mn': 2.44, 'Fe': 7.45, 'Si': 24.98, 'H': 0.18, 'O': 42.69}
	Found duplicates of "Schaferite", with these properties :
			Density None, Hardness 5.0, Elements {'Na': 4.63, 'Ca': 16.14, 'Mg': 9.79, 'V': 30.78, 'O': 38.66}
			Density None, Hardness 5.0, Elements {'Na': 4.63, 'Ca': 16.14, 'Mg': 9.79, 'V': 30.78, 'O': 38.66}
	Found duplicates of "Scheelite", with these properties :
			Density 6.01, Hardness 4.5, Elements {'Ca': 13.92, 'W': 63.85, 'O': 22.23}
			Density 6.01, Hardness 4.5, Elements {'Ca': 13.92, 'W': 63.85, 'O': 22.23}
	Found duplicates of "Scheuchzerite", with these properties :
			Density None, Hardness None, Elements {'Na': 1.7, 'Ca': 0.09, 'Mg': 1.76, 'Mn': 32.69, 'Al': 0.02, 'V': 3.7, 'Zn': 0.8, 'Si': 19.48, 'Ni': 0.18, 'As': 0.11, 'H': 0.31, 'O': 39.16}
			Density None, Hardness None, Elements {'Na': 1.7, 'Ca': 0.09, 'Mg': 1.76, 'Mn': 32.69, 'Al': 0.02, 'V': 3.7, 'Zn': 0.8, 'Si': 19.48, 'Ni': 0.18, 'As': 0.11, 'H': 0.31, 'O': 39.16}
	Found duplicates of "Schiavinatoite", with these properties :
			Density None, Hardness 8.0, Elements {'Ta': 41.36, 'Nb': 23.01, 'B': 5.15, 'O': 30.48}
			Density None, Hardness None, Elements {'Ta': 41.36, 'Nb': 23.01, 'B': 5.15, 'O': 30.48}
	Found duplicates of "Schlegelite", with these properties :
			Density None, Hardness 3.5, Elements {'Ca': 0.04, 'V': 0.11, 'Bi': 63.62, 'Mo': 8.18, 'As': 10.13, 'P': 0.22, 'Pb': 0.47, 'O': 17.23}
			Density None, Hardness 3.5, Elements {'Ca': 0.04, 'V': 0.11, 'Bi': 63.62, 'Mo': 8.18, 'As': 10.13, 'P': 0.22, 'Pb': 0.47, 'O': 17.23}
	Found duplicates of "Schlemaite", with these properties :
			Density None, Hardness 3.0, Elements {'Cu': 43.57, 'Ag': 2.88, 'Bi': 10.14, 'Pb': 4.02, 'Se': 39.38}
			Density None, Hardness 3.0, Elements {'Cu': 43.57, 'Ag': 2.88, 'Bi': 10.14, 'Pb': 4.02, 'Se': 39.38}
	Found duplicates of "Schmiederite", with these properties :
			Density 5.6, Hardness None, Elements {'Cu': 14.45, 'H': 0.46, 'Pb': 47.12, 'Se': 17.96, 'O': 20.01}
			Density 5.6, Hardness None, Elements {'Cu': 14.45, 'H': 0.46, 'Pb': 47.12, 'Se': 17.96, 'O': 20.01}
	Found duplicates of "Schneebergite", with these properties :
			Density None, Hardness 4.25, Elements {'Ca': 2.02, 'Fe': 1.88, 'Co': 10.91, 'Ni': 7.9, 'Bi': 24.63, 'As': 25.23, 'H': 0.49, 'O': 26.93}
			Density None, Hardness 4.25, Elements {'Ca': 2.02, 'Fe': 1.88, 'Co': 10.91, 'Ni': 7.9, 'Bi': 24.63, 'As': 25.23, 'H': 0.49, 'O': 26.93}
	Found duplicates of "Schneiderhohnite", with these properties :
			Density 4.3, Hardness 3.0, Elements {'Fe': 27.72, 'As': 46.48, 'O': 25.81}
			Density 4.3, Hardness 3.0, Elements {'Fe': 27.72, 'As': 46.48, 'O': 25.81}
	Found duplicates of "Schollhornite", with these properties :
			Density 2.7, Hardness 1.75, Elements {'Na': 4.89, 'Cr': 36.87, 'H': 1.43, 'S': 45.47, 'O': 11.34}
			Density 2.7, Hardness 1.75, Elements {'Na': 4.89, 'Cr': 36.87, 'H': 1.43, 'S': 45.47, 'O': 11.34}
	Found duplicates of "Schrockingerite", with these properties :
			Density 2.55, Hardness 2.5, Elements {'Na': 2.59, 'Ca': 13.53, 'U': 26.79, 'H': 2.27, 'C': 4.06, 'S': 3.61, 'O': 45.02, 'F': 2.14}
			Density 2.55, Hardness 2.5, Elements {'Na': 2.59, 'Ca': 13.53, 'U': 26.79, 'H': 2.27, 'C': 4.06, 'S': 3.61, 'O': 45.02, 'F': 2.14}
	Found duplicates of "Seelite-2", with these properties :
			Density 3.7, Hardness 3.0, Elements {'Mg': 4.43, 'U': 43.42, 'As': 13.67, 'H': 2.57, 'O': 35.9}
			Density 3.7, Hardness 3.0, Elements {'Mg': 4.43, 'U': 43.42, 'As': 13.67, 'H': 2.57, 'O': 35.9}
	Found duplicates of "Seidite-Ce", with these properties :
			Density 2.76, Hardness 3.5, Elements {'Na': 8.38, 'Sr': 3.99, 'Ce': 19.16, 'Ti': 4.36, 'Si': 20.48, 'H': 1.21, 'O': 41.71, 'F': 0.69}
			Density 2.76, Hardness 3.5, Elements {'Na': 8.38, 'Sr': 3.99, 'Ce': 19.16, 'Ti': 4.36, 'Si': 20.48, 'H': 1.21, 'O': 41.71, 'F': 0.69}
	Found duplicates of "Seifertite", with these properties :
			Density None, Hardness None, Elements {'Si': 46.74, 'O': 53.26}
			Density None, Hardness None, Elements {'Si': 46.74, 'O': 53.26}
	Found duplicates of "Selenium", with these properties :
			Density 4.81, Hardness 2.0, Elements {'Se': 100.0}
			Density 4.81, Hardness 2.0, Elements {'Se': 100.0}
	Found duplicates of "Selenojalpaite", with these properties :
			Density None, Hardness 4.25, Elements {'Cu': 11.78, 'Ag': 59.22, 'Se': 29.0}
			Density None, Hardness 4.25, Elements {'Cu': 11.78, 'Ag': 59.22, 'Se': 29.0}
	Found duplicates of "Selenopolybasite", with these properties :
			Density None, Hardness 3.25, Elements {'Zn': 0.03, 'Fe': 0.07, 'Cu': 3.21, 'Ag': 66.52, 'Bi': 0.09, 'Sb': 9.52, 'As': 0.6, 'Pb': 0.09, 'Se': 8.46, 'S': 11.42}
			Density None, Hardness 3.25, Elements {'Zn': 0.03, 'Fe': 0.07, 'Cu': 3.21, 'Ag': 66.52, 'Bi': 0.09, 'Sb': 9.52, 'As': 0.6, 'Pb': 0.09, 'Se': 8.46, 'S': 11.42}
			Density None, Hardness 3.25, Elements {'Zn': 0.03, 'Fe': 0.07, 'Cu': 3.21, 'Ag': 66.52, 'Bi': 0.09, 'Sb': 9.52, 'As': 0.6, 'Pb': 0.09, 'Se': 8.46, 'S': 11.42}
	Found duplicates of "Selwynite", with these properties :
			Density 2.94, Hardness 4.0, Elements {'K': 5.8, 'Na': 3.41, 'Zr': 27.07, 'Be': 1.0, 'Al': 1.0, 'P': 18.38, 'H': 0.6, 'O': 42.73}
			Density 2.94, Hardness 4.0, Elements {'K': 5.8, 'Na': 3.41, 'Zr': 27.07, 'Be': 1.0, 'Al': 1.0, 'P': 18.38, 'H': 0.6, 'O': 42.73}
	Found duplicates of "Senekevichite", with these properties :
			Density None, Hardness None, Elements {'Cs': 16.25, 'K': 4.78, 'Ca': 9.8, 'Ti': 5.86, 'Si': 24.04, 'H': 0.12, 'O': 39.14}
			Density None, Hardness None, Elements {'Cs': 16.25, 'K': 4.78, 'Ca': 9.8, 'Ti': 5.86, 'Si': 24.04, 'H': 0.12, 'O': 39.14}
	Found duplicates of "Serrabrancaite", with these properties :
			Density 3.17, Hardness 3.5, Elements {'Mn': 32.69, 'P': 18.81, 'H': 1.1, 'O': 47.4}
			Density 3.17, Hardness 3.5, Elements {'Mn': 32.69, 'P': 18.81, 'H': 1.1, 'O': 47.4}
	Found duplicates of "Sewardite", with these properties :
			Density None, Hardness 3.5, Elements {'Ca': 8.62, 'Zn': 1.41, 'Fe': 22.83, 'As': 32.25, 'H': 0.46, 'O': 34.43}
			Density None, Hardness 3.5, Elements {'Ca': 8.62, 'Zn': 1.41, 'Fe': 22.83, 'As': 32.25, 'H': 0.46, 'O': 34.43}
	Found duplicates of "Shakhovite", with these properties :
			Density 8.42, Hardness 3.25, Elements {'Hg': 78.42, 'Sb': 11.9, 'H': 0.3, 'O': 9.38}
			Density 8.42, Hardness 3.25, Elements {'Hg': 78.42, 'Sb': 11.9, 'H': 0.3, 'O': 9.38}
	Found duplicates of "Shannonite", with these properties :
			Density 9.31, Hardness 3.25, Elements {'Pb': 84.5, 'C': 2.45, 'O': 13.05}
			Density 9.31, Hardness 3.25, Elements {'Pb': 84.5, 'C': 2.45, 'O': 13.05}
	Found duplicates of "Sheldrickite", with these properties :
			Density 2.86, Hardness 3.0, Elements {'Na': 6.8, 'Ca': 35.55, 'H': 0.6, 'C': 7.1, 'O': 33.11, 'F': 16.85}
			Density 2.86, Hardness 3.0, Elements {'Na': 6.8, 'Ca': 35.55, 'H': 0.6, 'C': 7.1, 'O': 33.11, 'F': 16.85}
	Found duplicates of "Shibkovite", with these properties :
			Density None, Hardness 5.75, Elements {'K': 7.29, 'Na': 0.58, 'Ca': 4.41, 'Mn': 1.86, 'Zn': 16.62, 'Si': 28.56, 'O': 40.67}
			Density None, Hardness 5.75, Elements {'K': 7.29, 'Na': 0.58, 'Ca': 4.41, 'Mn': 1.86, 'Zn': 16.62, 'Si': 28.56, 'O': 40.67}
	Found duplicates of "Shirokshinite", with these properties :
			Density None, Hardness 2.5, Elements {'K': 9.29, 'Na': 5.46, 'Mg': 11.55, 'Si': 26.68, 'O': 38.0, 'F': 9.02}
			Density None, Hardness 2.5, Elements {'K': 9.29, 'Na': 5.46, 'Mg': 11.55, 'Si': 26.68, 'O': 38.0, 'F': 9.02}
	Found duplicates of "Shirozulite", with these properties :
			Density 3.2, Hardness 3.0, Elements {'K': 7.32, 'Ba': 2.57, 'Mg': 4.76, 'Ti': 0.4, 'Mn': 17.5, 'Al': 9.88, 'Fe': 2.32, 'Si': 14.85, 'H': 0.41, 'O': 39.86, 'F': 0.12}
			Density 3.2, Hardness 3.0, Elements {'K': 7.32, 'Ba': 2.57, 'Mg': 4.76, 'Ti': 0.4, 'Mn': 17.5, 'Al': 9.88, 'Fe': 2.32, 'Si': 14.85, 'H': 0.41, 'O': 39.86, 'F': 0.12}
	Found duplicates of "Shkatulkalite", with these properties :
			Density 2.7, Hardness 3.0, Elements {'Na': 5.82, 'Ca': 0.92, 'Mn': 5.06, 'Nb': 17.11, 'Si': 10.34, 'H': 2.41, 'O': 57.46, 'F': 0.87}
			Density 2.7, Hardness 3.0, Elements {'Na': 5.82, 'Ca': 0.92, 'Mn': 5.06, 'Nb': 17.11, 'Si': 10.34, 'H': 2.41, 'O': 57.46, 'F': 0.87}
	Found duplicates of "Shuangfengite", with these properties :
			Density 10.14, Hardness 3.0, Elements {'Te': 57.04, 'Ir': 42.96}
			Density 10.14, Hardness 3.0, Elements {'Te': 57.04, 'Ir': 42.96}
	Found duplicates of "Sicherite", with these properties :
			Density None, Hardness 3.0, Elements {'Tl': 23.27, 'Ag': 24.56, 'Sb': 11.09, 'As': 19.19, 'S': 21.9}
			Density None, Hardness 3.0, Elements {'Tl': 23.27, 'Ag': 24.56, 'Sb': 11.09, 'As': 19.19, 'S': 21.9}
	Found duplicates of "Siderite", with these properties :
			Density 3.96, Hardness 3.5, Elements {'Fe': 48.2, 'C': 10.37, 'O': 41.43}
			Density 3.96, Hardness 3.5, Elements {'Fe': 48.2, 'C': 10.37, 'O': 41.43}
			Density 3.96, Hardness 3.5, Elements {'Fe': 48.2, 'C': 10.37, 'O': 41.43}
	Found duplicates of "Sidpietersite", with these properties :
			Density None, Hardness 1.5, Elements {'H': 0.2, 'Pb': 82.31, 'S': 6.37, 'O': 11.12}
			Density None, Hardness 1.5, Elements {'H': 0.2, 'Pb': 82.31, 'S': 6.37, 'O': 11.12}
	Found duplicates of "Silicon", with these properties :
			Density None, Hardness 7.0, Elements {'Si': 100.0}
			Density None, Hardness 7.0, Elements {'Si': 100.0}
	Found duplicates of "Sillimanite", with these properties :
			Density 3.24, Hardness 7.0, Elements {'Al': 33.3, 'Si': 17.33, 'O': 49.37}
			Density 3.24, Hardness 7.0, Elements {'Al': 33.3, 'Si': 17.33, 'O': 49.37}
	Found duplicates of "Silver", with these properties :
			Density 10.5, Hardness 2.75, Elements {'Ag': 100.0}
			Density 10.5, Hardness 2.75, Elements {'Ag': 100.0}
	Found duplicates of "Silvialite", with these properties :
			Density 2.75, Hardness 5.5, Elements {'Na': 2.45, 'Ca': 12.8, 'Al': 17.24, 'Si': 17.94, 'C': 0.51, 'S': 2.05, 'O': 47.01}
			Density 2.75, Hardness 5.5, Elements {'Na': 2.45, 'Ca': 12.8, 'Al': 17.24, 'Si': 17.94, 'C': 0.51, 'S': 2.05, 'O': 47.01}
	Found duplicates of "Simmonsite", with these properties :
			Density 3.05, Hardness 2.75, Elements {'Na': 23.71, 'Li': 3.58, 'Al': 13.92, 'F': 58.79}
			Density 3.05, Hardness 2.75, Elements {'Na': 23.71, 'Li': 3.58, 'Al': 13.92, 'F': 58.79}
	Found duplicates of "Chloritoid", with these properties :
			Density 3.54, Hardness 6.5, Elements {'Mg': 3.01, 'Mn': 2.27, 'Al': 22.27, 'Fe': 13.83, 'Si': 11.59, 'H': 0.83, 'O': 46.21}
			Density 3.54, Hardness 6.5, Elements {'Mg': 3.01, 'Mn': 2.27, 'Al': 22.27, 'Fe': 13.83, 'Si': 11.59, 'H': 0.83, 'O': 46.21}
	Found duplicates of "Sjogrenite", with these properties :
			Density 2.11, Hardness 2.5, Elements {'Mg': 22.04, 'Fe': 16.88, 'H': 3.66, 'C': 1.82, 'O': 55.61}
			Density 2.11, Hardness 2.5, Elements {'Mg': 22.04, 'Fe': 16.88, 'H': 3.66, 'C': 1.82, 'O': 55.61}
	Found duplicates of "Skaergaardite", with these properties :
			Density None, Hardness 4.5, Elements {'Zn': 1.47, 'Fe': 3.87, 'Cu': 30.09, 'Sn': 1.1, 'Te': 0.29, 'Pd': 59.42, 'Pt': 1.13, 'Pb': 0.36, 'Au': 2.27}
			Density None, Hardness 4.5, Elements {'Zn': 1.47, 'Fe': 3.87, 'Cu': 30.09, 'Sn': 1.1, 'Te': 0.29, 'Pd': 59.42, 'Pt': 1.13, 'Pb': 0.36, 'Au': 2.27}
	Found duplicates of "Sklodowskite", with these properties :
			Density 3.54, Hardness 2.5, Elements {'Mg': 2.83, 'U': 55.44, 'Si': 6.54, 'H': 1.64, 'O': 33.54}
			Density 3.54, Hardness 2.5, Elements {'Mg': 2.83, 'U': 55.44, 'Si': 6.54, 'H': 1.64, 'O': 33.54}
	Found duplicates of "Skorpionite", with these properties :
			Density 3.15, Hardness None, Elements {'Ca': 22.14, 'Zn': 23.21, 'P': 11.16, 'H': 0.73, 'C': 2.18, 'O': 40.57}
			Density 3.15, Hardness None, Elements {'Ca': 22.14, 'Zn': 23.21, 'P': 11.16, 'H': 0.73, 'C': 2.18, 'O': 40.57}
	Found duplicates of "Skutterudite", with these properties :
			Density 6.5, Hardness 5.75, Elements {'Co': 17.95, 'Ni': 5.96, 'As': 76.09}
			Density 6.5, Hardness 5.75, Elements {'Co': 17.95, 'Ni': 5.96, 'As': 76.09}
	Found duplicates of "Slavkovite", with these properties :
			Density None, Hardness None, Elements {'Cu': 31.37, 'As': 28.45, 'H': 1.91, 'O': 38.27}
			Density None, Hardness None, Elements {'Cu': 31.37, 'As': 28.45, 'H': 1.91, 'O': 38.27}
	Found duplicates of "Smrkovecite", with these properties :
			Density None, Hardness 4.5, Elements {'Bi': 76.56, 'P': 5.67, 'H': 0.18, 'O': 17.58}
			Density None, Hardness 4.5, Elements {'Bi': 76.56, 'P': 5.67, 'H': 0.18, 'O': 17.58}
	Found duplicates of "Sodic-ferri-ferropedrizite", with these properties :
			Density None, Hardness None, Elements {'Na': 1.81, 'Li': 1.96, 'Mg': 2.71, 'Ti': 0.73, 'Mn': 0.45, 'Al': 0.67, 'Fe': 19.93, 'Si': 26.38, 'H': 0.17, 'O': 44.04, 'F': 1.16}
			Density None, Hardness None, Elements {'Na': 1.81, 'Li': 1.96, 'Mg': 2.71, 'Ti': 0.73, 'Mn': 0.45, 'Al': 0.67, 'Fe': 19.93, 'Si': 26.38, 'H': 0.17, 'O': 44.04, 'F': 1.16}
	Found duplicates of "Ferri-clinoholmquistite", with these properties :
			Density 3.19, Hardness 6.0, Elements {'K': 0.14, 'Na': 1.98, 'Li': 1.76, 'Ca': 0.19, 'Mg': 3.46, 'Ti': 0.68, 'Al': 0.64, 'Zn': 0.15, 'Fe': 19.0, 'Si': 26.46, 'H': 0.19, 'O': 44.42, 'F': 0.94}
			Density 3.19, Hardness 6.0, Elements {'K': 0.14, 'Na': 1.98, 'Li': 1.76, 'Ca': 0.19, 'Mg': 3.46, 'Ti': 0.68, 'Al': 0.64, 'Zn': 0.15, 'Fe': 19.0, 'Si': 26.46, 'H': 0.19, 'O': 44.42, 'F': 0.94}
			Density 3.19, Hardness 6.0, Elements {'K': 0.14, 'Na': 1.98, 'Li': 1.76, 'Ca': 0.19, 'Mg': 3.46, 'Ti': 0.68, 'Al': 0.64, 'Zn': 0.15, 'Fe': 19.0, 'Si': 26.46, 'H': 0.19, 'O': 44.42, 'F': 0.94}
			Density 3.19, Hardness 6.0, Elements {'K': 0.14, 'Na': 1.98, 'Li': 1.76, 'Ca': 0.19, 'Mg': 3.46, 'Ti': 0.68, 'Al': 0.64, 'Zn': 0.15, 'Fe': 19.0, 'Si': 26.46, 'H': 0.19, 'O': 44.42, 'F': 0.94}
	Found duplicates of "Sodic-ferripedrizite", with these properties :
			Density 3.15, Hardness 6.0, Elements {'K': 0.14, 'Na': 3.63, 'Li': 1.9, 'Ca': 0.4, 'Mg': 5.24, 'Ti': 0.65, 'Mn': 0.47, 'Al': 0.7, 'Zn': 0.08, 'Fe': 11.36, 'Si': 27.69, 'H': 0.17, 'O': 46.05, 'F': 1.52}
			Density 3.15, Hardness 6.0, Elements {'K': 0.14, 'Na': 3.63, 'Li': 1.9, 'Ca': 0.4, 'Mg': 5.24, 'Ti': 0.65, 'Mn': 0.47, 'Al': 0.7, 'Zn': 0.08, 'Fe': 11.36, 'Si': 27.69, 'H': 0.17, 'O': 46.05, 'F': 1.52}
	Found duplicates of "Sodicanthophyllite", with these properties :
			Density None, Hardness 5.5, Elements {'Na': 2.86, 'Mg': 21.17, 'Si': 27.95, 'H': 0.25, 'O': 47.77}
			Density None, Hardness 5.5, Elements {'Na': 2.86, 'Mg': 21.17, 'Si': 27.95, 'H': 0.25, 'O': 47.77}
	Found duplicates of "Sodicgedrite", with these properties :
			Density None, Hardness 5.5, Elements {'Na': 2.86, 'Mg': 18.13, 'Al': 10.06, 'Si': 20.95, 'H': 0.25, 'O': 47.74}
			Density None, Hardness 5.5, Elements {'Na': 2.86, 'Mg': 18.13, 'Al': 10.06, 'Si': 20.95, 'H': 0.25, 'O': 47.74}
	Found duplicates of "Sohngeite", with these properties :
			Density 3.84, Hardness 4.25, Elements {'Ga': 57.74, 'H': 2.5, 'O': 39.75}
			Density 3.84, Hardness 4.25, Elements {'Ga': 57.74, 'H': 2.5, 'O': 39.75}
	Found duplicates of "Sophiite", with these properties :
			Density None, Hardness 2.25, Elements {'Zn': 39.79, 'Se': 24.03, 'Cl': 21.58, 'O': 14.6}
			Density None, Hardness 2.25, Elements {'Zn': 39.79, 'Se': 24.03, 'Cl': 21.58, 'O': 14.6}
	Found duplicates of "Sokolovaite", with these properties :
			Density None, Hardness None, Elements {'Cs': 27.45, 'Li': 2.87, 'Al': 5.57, 'Si': 23.21, 'O': 33.05, 'F': 7.85}
			Density None, Hardness None, Elements {'Cs': 27.45, 'Li': 2.87, 'Al': 5.57, 'Si': 23.21, 'O': 33.05, 'F': 7.85}
	Found duplicates of "Sorosite", with these properties :
			Density 7.75, Hardness 5.25, Elements {'Cu': 34.72, 'Sn': 48.65, 'Sb': 16.63}
			Density 7.75, Hardness 5.25, Elements {'Cu': 34.72, 'Sn': 48.65, 'Sb': 16.63}
	Found duplicates of "Spessartine", with these properties :
			Density 4.18, Hardness 7.0, Elements {'Mn': 33.29, 'Al': 10.9, 'Si': 17.02, 'O': 38.78}
			Density 4.18, Hardness 7.0, Elements {'Mn': 33.29, 'Al': 10.9, 'Si': 17.02, 'O': 38.78}
	Found duplicates of "Sphaerobismoite", with these properties :
			Density 7.17, Hardness 4.0, Elements {'Bi': 92.89, 'O': 7.11}
			Density 7.17, Hardness 4.0, Elements {'Bi': 92.89, 'O': 7.11}
	Found duplicates of "Sphaerocobaltite", with these properties :
			Density 4.1, Hardness 3.5, Elements {'Co': 49.55, 'C': 10.1, 'O': 40.35}
			Density 4.1, Hardness 3.5, Elements {'Co': 49.55, 'C': 10.1, 'O': 40.35}
	Found duplicates of "Spriggite", with these properties :
			Density None, Hardness 4.0, Elements {'Ba': 0.23, 'Ca': 0.1, 'U': 59.1, 'H': 0.33, 'Pb': 23.76, 'O': 16.48}
			Density None, Hardness 4.0, Elements {'Ba': 0.23, 'Ca': 0.1, 'U': 59.1, 'H': 0.33, 'Pb': 23.76, 'O': 16.48}
	Found duplicates of "Springcreekite", with these properties :
			Density 3.48, Hardness 4.5, Elements {'Ba': 23.53, 'V': 26.18, 'P': 10.61, 'H': 1.3, 'O': 38.38}
			Density 3.48, Hardness 4.5, Elements {'Ba': 23.53, 'V': 26.18, 'P': 10.61, 'H': 1.3, 'O': 38.38}
	Found duplicates of "Sreinite", with these properties :
			Density None, Hardness None, Elements {'U': 42.99, 'Bi': 27.45, 'P': 2.54, 'H': 0.51, 'Pb': 6.8, 'O': 19.7}
			Density None, Hardness None, Elements {'U': 42.99, 'Bi': 27.45, 'P': 2.54, 'H': 0.51, 'Pb': 6.8, 'O': 19.7}
	Found duplicates of "Stanekite", with these properties :
			Density 3.8, Hardness 4.5, Elements {'Mg': 1.11, 'Mn': 15.05, 'Fe': 33.16, 'P': 14.15, 'O': 36.53}
			Density None, Hardness None, Elements {'Mg': 1.11, 'Mn': 15.05, 'Fe': 33.16, 'P': 14.15, 'O': 36.53}
	Found duplicates of "Enargite", with these properties :
			Density 4.45, Hardness 3.0, Elements {'Cu': 48.41, 'As': 19.02, 'S': 32.57}
			Density 4.45, Hardness 3.0, Elements {'Cu': 48.41, 'As': 19.02, 'S': 32.57}
	Found duplicates of "Staurolite", with these properties :
			Density 3.71, Hardness 7.25, Elements {'Li': 0.09, 'Mg': 0.3, 'Al': 28.91, 'Fe': 9.63, 'Si': 13.49, 'H': 0.29, 'O': 47.3}
			Density 3.71, Hardness 7.25, Elements {'Li': 0.09, 'Mg': 0.3, 'Al': 28.91, 'Fe': 9.63, 'Si': 13.49, 'H': 0.29, 'O': 47.3}
	Found duplicates of "Stavelotite-La", with these properties :
			Density None, Hardness None, Elements {'Ca': 0.24, 'La': 7.38, 'Ce': 0.39, 'Mg': 0.66, 'Sc': 0.99, 'Ti': 0.27, 'Mn': 30.7, 'Al': 1.8, 'Fe': 9.44, 'Co': 0.15, 'Cu': 1.74, 'Si': 9.73, 'Nd': 3.0, 'O': 33.5}
			Density None, Hardness None, Elements {'Ca': 0.24, 'La': 7.38, 'Ce': 0.39, 'Mg': 0.66, 'Sc': 0.99, 'Ti': 0.27, 'Mn': 30.7, 'Al': 1.8, 'Fe': 9.44, 'Co': 0.15, 'Cu': 1.74, 'Si': 9.73, 'Nd': 3.0, 'O': 33.5}
	Found duplicates of "Steacyite", with these properties :
			Density 2.95, Hardness 5.0, Elements {'K': 2.84, 'Na': 1.95, 'Ca': 3.4, 'Th': 25.29, 'Mn': 1.33, 'Si': 27.21, 'O': 37.98}
			Density 2.95, Hardness 5.0, Elements {'K': 2.84, 'Na': 1.95, 'Ca': 3.4, 'Th': 25.29, 'Mn': 1.33, 'Si': 27.21, 'O': 37.98}
	Found duplicates of "Cavoite", with these properties :
			Density None, Hardness None, Elements {'K': 0.26, 'Ca': 12.7, 'Mn': 0.18, 'V': 47.42, 'Si': 2.06, 'O': 37.37}
			Density None, Hardness None, Elements {'K': 0.26, 'Ca': 12.7, 'Mn': 0.18, 'V': 47.42, 'Si': 2.06, 'O': 37.37}
	Found duplicates of "Cejkaite", with these properties :
			Density 3.67, Hardness None, Elements {'Na': 15.86, 'Mg': 0.09, 'U': 44.87, 'Fe': 0.41, 'C': 6.55, 'O': 32.21}
			Density 3.67, Hardness None, Elements {'Na': 15.86, 'Mg': 0.09, 'U': 44.87, 'Fe': 0.41, 'C': 6.55, 'O': 32.21}
	Found duplicates of "Celestine", with these properties :
			Density 3.95, Hardness 3.25, Elements {'Sr': 47.7, 'S': 17.46, 'O': 34.84}
			Density 3.95, Hardness 3.25, Elements {'Sr': 47.7, 'S': 17.46, 'O': 34.84}
	Found duplicates of "Chlorargyrite", with these properties :
			Density 5.55, Hardness 1.25, Elements {'Ag': 75.26, 'Cl': 24.74}
			Density 5.55, Hardness 1.25, Elements {'Ag': 75.26, 'Cl': 24.74}
	Found duplicates of "Cerchiaraite", with these properties :
			Density None, Hardness 4.5, Elements {'Ba': 39.27, 'Mn': 13.35, 'Al': 0.58, 'Fe': 1.6, 'Si': 12.05, 'H': 0.52, 'Cl': 3.8, 'O': 28.83}
			Density None, Hardness 4.5, Elements {'Ba': 39.27, 'Mn': 13.35, 'Al': 0.58, 'Fe': 1.6, 'Si': 12.05, 'H': 0.52, 'Cl': 3.8, 'O': 28.83}
	Found duplicates of "Ferriallanite-Ce", with these properties :
			Density 4.22, Hardness 6.0, Elements {'Ca': 7.05, 'Ce': 21.0, 'Ti': 1.09, 'Mn': 0.63, 'Al': 2.99, 'Fe': 19.74, 'Si': 13.45, 'H': 0.16, 'O': 33.88}
			Density 4.22, Hardness 6.0, Elements {'Ca': 7.05, 'Ce': 21.0, 'Ti': 1.09, 'Mn': 0.63, 'Al': 2.99, 'Fe': 19.74, 'Si': 13.45, 'H': 0.16, 'O': 33.88}
			Density 4.22, Hardness 6.0, Elements {'Ca': 7.05, 'Ce': 21.0, 'Ti': 1.09, 'Mn': 0.63, 'Al': 2.99, 'Fe': 19.74, 'Si': 13.45, 'H': 0.16, 'O': 33.88}
	Found duplicates of "Cerite-La", with these properties :
			Density 4.7, Hardness 5.0, Elements {'Sr': 1.68, 'Ca': 3.69, 'La': 32.41, 'Ce': 20.49, 'Pr': 0.54, 'Sm': 0.08, 'Gd': 0.17, 'Mg': 0.31, 'Fe': 0.98, 'Si': 9.78, 'P': 1.09, 'H': 0.36, 'Nd': 1.26, 'O': 27.16}
			Density 4.7, Hardness 5.0, Elements {'Sr': 1.68, 'Ca': 3.69, 'La': 32.41, 'Ce': 20.49, 'Pr': 0.54, 'Sm': 0.08, 'Gd': 0.17, 'Mg': 0.31, 'Fe': 0.98, 'Si': 9.78, 'P': 1.09, 'H': 0.36, 'Nd': 1.26, 'O': 27.16}
	Found duplicates of "Chabazite-Sr", with these properties :
			Density 2.16, Hardness 4.25, Elements {'K': 2.19, 'Na': 0.86, 'Sr': 8.2, 'Ca': 3.75, 'Al': 11.61, 'Si': 18.92, 'H': 2.07, 'O': 52.39}
			Density 2.16, Hardness 4.25, Elements {'K': 2.19, 'Na': 0.86, 'Sr': 8.2, 'Ca': 3.75, 'Al': 11.61, 'Si': 18.92, 'H': 2.07, 'O': 52.39}
	Found duplicates of "Chadwickite", with these properties :
			Density 4.86, Hardness 2.0, Elements {'U': 60.42, 'As': 19.02, 'H': 0.26, 'O': 20.31}
			Density 4.86, Hardness 2.0, Elements {'U': 60.42, 'As': 19.02, 'H': 0.26, 'O': 20.31}
	Found duplicates of "Chalcocite", with these properties :
			Density 5.65, Hardness 2.75, Elements {'Cu': 79.85, 'S': 20.15}
			Density 5.65, Hardness 2.75, Elements {'Cu': 79.85, 'S': 20.15}
	Found duplicates of "Cuprite", with these properties :
			Density 6.1, Hardness 3.75, Elements {'Cu': 88.82, 'O': 11.18}
			Density 6.1, Hardness 3.75, Elements {'Cu': 88.82, 'O': 11.18}
	Found duplicates of "Challacolloite", with these properties :
			Density None, Hardness 2.5, Elements {'K': 6.32, 'Pb': 65.69, 'Cl': 27.99}
			Density None, Hardness 2.5, Elements {'K': 6.32, 'Pb': 65.69, 'Cl': 27.99}
	Found duplicates of "Cubanite", with these properties :
			Density 4.7, Hardness 3.5, Elements {'Fe': 41.15, 'Cu': 23.41, 'S': 35.44}
			Density 4.7, Hardness 3.5, Elements {'Fe': 41.15, 'Cu': 23.41, 'S': 35.44}
	Found duplicates of "Changchengite", with these properties :
			Density 12.11, Hardness 3.5, Elements {'Bi': 48.23, 'Ir': 44.37, 'S': 7.4}
			Density 12.11, Hardness 3.5, Elements {'Bi': 48.23, 'Ir': 44.37, 'S': 7.4}
	Found duplicates of "Changoite", with these properties :
			Density 2.5, Hardness None, Elements {'Na': 12.24, 'Zn': 17.41, 'H': 2.15, 'S': 17.08, 'O': 51.12}
			Density 2.5, Hardness None, Elements {'Na': 12.24, 'Zn': 17.41, 'H': 2.15, 'S': 17.08, 'O': 51.12}
	Found duplicates of "Charmarite-2H", with these properties :
			Density 2.5, Hardness 2.5, Elements {'Mn': 37.13, 'Al': 9.12, 'H': 3.07, 'C': 2.03, 'O': 48.66}
			Density 2.5, Hardness 2.5, Elements {'Mn': 37.13, 'Al': 9.12, 'H': 3.07, 'C': 2.03, 'O': 48.66}
	Found duplicates of "Charmarite-3T", with these properties :
			Density 2.5, Hardness 2.0, Elements {'Mn': 37.13, 'Al': 9.12, 'H': 3.07, 'C': 2.03, 'O': 48.66}
			Density 2.5, Hardness 2.0, Elements {'Mn': 37.13, 'Al': 9.12, 'H': 3.07, 'C': 2.03, 'O': 48.66}
	Found duplicates of "Chengdeite", with these properties :
			Density 19.3, Hardness 5.0, Elements {'Fe': 8.83, 'Ir': 91.17}
			Density 19.3, Hardness 5.0, Elements {'Fe': 8.83, 'Ir': 91.17}
	Found duplicates of "Chenguodaite", with these properties :
			Density None, Hardness 2.5, Elements {'Fe': 3.97, 'Ag': 68.77, 'Te': 18.05, 'S': 9.21}
			Density None, Hardness 2.5, Elements {'Fe': 3.97, 'Ag': 68.77, 'Te': 18.05, 'S': 9.21}
	Found duplicates of "Chesnokovite", with these properties :
			Density 1.68, Hardness 2.5, Elements {'K': 0.28, 'Na': 16.57, 'Si': 10.17, 'H': 6.23, 'O': 66.75}
			Density 1.68, Hardness 2.5, Elements {'K': 0.28, 'Na': 16.57, 'Si': 10.17, 'H': 6.23, 'O': 66.75}
	Found duplicates of "Chiluite", with these properties :
			Density 3.65, Hardness 3.0, Elements {'Bi': 61.56, 'Te': 12.53, 'Mo': 9.42, 'O': 16.49}
			Density 3.65, Hardness 3.0, Elements {'Bi': 61.56, 'Te': 12.53, 'Mo': 9.42, 'O': 16.49}
	Found duplicates of "Chistyakovaite-Y", with these properties :
			Density 3.62, Hardness 2.5, Elements {'U': 49.1, 'Al': 2.67, 'As': 14.14, 'P': 0.54, 'H': 1.38, 'O': 31.04, 'F': 1.12}
			Density 3.62, Hardness 2.5, Elements {'U': 49.1, 'Al': 2.67, 'As': 14.14, 'P': 0.54, 'H': 1.38, 'O': 31.04, 'F': 1.12}
	Found duplicates of "Chivruaiite", with these properties :
			Density 2.41, Hardness 3.0, Elements {'K': 1.09, 'Na': 0.07, 'Sr': 0.22, 'Ca': 7.58, 'Ti': 12.47, 'Mn': 0.03, 'Nb': 2.58, 'Al': 0.03, 'Fe': 0.14, 'Si': 21.26, 'H': 1.95, 'O': 52.56}
			Density 2.41, Hardness 3.0, Elements {'K': 1.09, 'Na': 0.07, 'Sr': 0.22, 'Ca': 7.58, 'Ti': 12.47, 'Mn': 0.03, 'Nb': 2.58, 'Al': 0.03, 'Fe': 0.14, 'Si': 21.26, 'H': 1.95, 'O': 52.56}
	Found duplicates of "Chladniite", with these properties :
			Density 3.01, Hardness 4.0, Elements {'Na': 4.88, 'Ca': 4.73, 'Mg': 20.08, 'Fe': 1.98, 'Si': 0.33, 'P': 21.93, 'O': 46.07}
			Density 3.01, Hardness 4.0, Elements {'Na': 4.88, 'Ca': 4.73, 'Mg': 20.08, 'Fe': 1.98, 'Si': 0.33, 'P': 21.93, 'O': 46.07}
	Found duplicates of "Chlorartinite", with these properties :
			Density 1.87, Hardness None, Elements {'Mg': 22.6, 'H': 3.28, 'C': 5.58, 'Cl': 16.48, 'O': 52.06}
			Density 1.87, Hardness None, Elements {'Mg': 22.6, 'H': 3.28, 'C': 5.58, 'Cl': 16.48, 'O': 52.06}
	Found duplicates of "Chlorbartonite", with these properties :
			Density 3.72, Hardness 4.0, Elements {'K': 9.72, 'Fe': 54.63, 'Cu': 0.52, 'S': 34.11, 'Cl': 1.01}
			Density 3.72, Hardness 4.0, Elements {'K': 9.72, 'Fe': 54.63, 'Cu': 0.52, 'S': 34.11, 'Cl': 1.01}
	Found duplicates of "Ellestadite-Cl", with these properties :
			Density 3.07, Hardness 4.5, Elements {'Ca': 39.16, 'Si': 5.49, 'P': 6.05, 'H': 0.06, 'S': 6.27, 'Cl': 4.16, 'O': 38.45, 'F': 0.37}
			Density 3.07, Hardness 4.5, Elements {'Ca': 39.16, 'Si': 5.49, 'P': 6.05, 'H': 0.06, 'S': 6.27, 'Cl': 4.16, 'O': 38.45, 'F': 0.37}
	Found duplicates of "Donbassite", with these properties :
			Density 2.63, Hardness 2.25, Elements {'Al': 27.45, 'Si': 16.07, 'H': 1.54, 'O': 54.94}
			Density 2.63, Hardness 2.25, Elements {'Al': 27.45, 'Si': 16.07, 'H': 1.54, 'O': 54.94}
	Found duplicates of "Chlormagaluminite", with these properties :
			Density 2.03, Hardness 2.0, Elements {'Mg': 18.0, 'Al': 11.42, 'Fe': 5.91, 'H': 3.41, 'C': 1.27, 'Cl': 7.5, 'O': 52.48}
			Density 2.03, Hardness 2.0, Elements {'Mg': 18.0, 'Al': 11.42, 'Fe': 5.91, 'H': 3.41, 'C': 1.27, 'Cl': 7.5, 'O': 52.48}
	Found duplicates of "Chloro-potassichastingsite", with these properties :
			Density 3.52, Hardness 5.0, Elements {'K': 2.5, 'Na': 0.78, 'Ca': 7.6, 'Mg': 1.75, 'Ti': 0.24, 'Mn': 0.33, 'Al': 5.84, 'Fe': 23.52, 'Si': 16.51, 'H': 0.06, 'Cl': 4.63, 'O': 36.11, 'F': 0.13}
			Density 3.52, Hardness 5.0, Elements {'K': 2.5, 'Na': 0.78, 'Ca': 7.6, 'Mg': 1.75, 'Ti': 0.24, 'Mn': 0.33, 'Al': 5.84, 'Fe': 23.52, 'Si': 16.51, 'H': 0.06, 'Cl': 4.63, 'O': 36.11, 'F': 0.13}
			Density 3.52, Hardness 5.0, Elements {'K': 2.5, 'Na': 0.78, 'Ca': 7.6, 'Mg': 1.75, 'Ti': 0.24, 'Mn': 0.33, 'Al': 5.84, 'Fe': 23.52, 'Si': 16.51, 'H': 0.06, 'Cl': 4.63, 'O': 36.11, 'F': 0.13}
	Found duplicates of "Chloromenite", with these properties :
			Density None, Hardness 2.0, Elements {'Cu': 43.18, 'Se': 23.85, 'Cl': 16.06, 'O': 16.91}
			Density None, Hardness 2.0, Elements {'Cu': 43.18, 'Se': 23.85, 'Cl': 16.06, 'O': 16.91}
	Found duplicates of "Chopinite", with these properties :
			Density None, Hardness None, Elements {'Ca': 0.27, 'Mg': 18.32, 'Mn': 0.19, 'Fe': 16.46, 'Si': 0.19, 'P': 20.7, 'O': 43.86}
			Density None, Hardness None, Elements {'Ca': 0.27, 'Mg': 18.32, 'Mn': 0.19, 'Fe': 16.46, 'Si': 0.19, 'P': 20.7, 'O': 43.86}
	Found duplicates of "Chrisstanleyite", with these properties :
			Density 8.31, Hardness 5.0, Elements {'Cu': 3.13, 'Ag': 20.58, 'Pd': 38.69, 'Se': 37.61}
			Density 8.31, Hardness 5.0, Elements {'Cu': 3.13, 'Ag': 20.58, 'Pd': 38.69, 'Se': 37.61}
	Found duplicates of "Christelite", with these properties :
			Density 3.06, Hardness 2.5, Elements {'Zn': 28.45, 'Cu': 18.43, 'H': 2.05, 'S': 9.3, 'O': 41.77}
			Density 3.06, Hardness 2.5, Elements {'Zn': 28.45, 'Cu': 18.43, 'H': 2.05, 'S': 9.3, 'O': 41.77}
	Found duplicates of "Chrombismite", with these properties :
			Density 9.8, Hardness 3.25, Elements {'Cr': 1.36, 'Bi': 87.36, 'O': 11.29}
			Density 9.8, Hardness 3.25, Elements {'Cr': 1.36, 'Bi': 87.36, 'O': 11.29}
	Found duplicates of "Chromceladonite", with these properties :
			Density 2.9, Hardness 1.5, Elements {'K': 9.27, 'Mg': 5.76, 'Cr': 12.33, 'Si': 26.64, 'H': 0.48, 'O': 45.52}
			Density 2.9, Hardness 1.5, Elements {'K': 9.27, 'Mg': 5.76, 'Cr': 12.33, 'Si': 26.64, 'H': 0.48, 'O': 45.52}
	Found duplicates of "Chromite", with these properties :
			Density 4.79, Hardness 5.5, Elements {'Cr': 46.46, 'Fe': 24.95, 'O': 28.59}
			Density 4.79, Hardness 5.5, Elements {'Cr': 46.46, 'Fe': 24.95, 'O': 28.59}
			Density 4.79, Hardness 5.5, Elements {'Cr': 46.46, 'Fe': 24.95, 'O': 28.59}
			Density 4.79, Hardness 5.5, Elements {'Cr': 46.46, 'Fe': 24.95, 'O': 28.59}
	Found duplicates of "Chromphyllite", with these properties :
			Density 2.88, Hardness 3.0, Elements {'K': 7.06, 'Ba': 8.26, 'Al': 8.11, 'Cr': 9.38, 'Si': 20.27, 'H': 0.36, 'O': 44.27, 'F': 2.29}
			Density 2.88, Hardness 3.0, Elements {'K': 7.06, 'Ba': 8.26, 'Al': 8.11, 'Cr': 9.38, 'Si': 20.27, 'H': 0.36, 'O': 44.27, 'F': 2.29}
	Found duplicates of "Chukanovite", with these properties :
			Density None, Hardness 3.75, Elements {'Mg': 0.12, 'Fe': 53.18, 'Ni': 0.57, 'H': 1.22, 'C': 5.4, 'O': 39.52}
			Density None, Hardness 3.75, Elements {'Mg': 0.12, 'Fe': 53.18, 'Ni': 0.57, 'H': 1.22, 'C': 5.4, 'O': 39.52}
	Found duplicates of "Chukhrovite-Nd", with these properties :
			Density 2.42, Hardness 3.75, Elements {'Ca': 14.31, 'La': 1.94, 'Ce': 1.14, 'Pr': 1.15, 'Sm': 1.58, 'Gd': 0.92, 'Dy': 0.38, 'Y': 1.56, 'Ho': 0.19, 'Al': 6.39, 'H': 2.79, 'S': 3.78, 'Nd': 5.38, 'O': 29.54, 'F': 28.94}
			Density 2.42, Hardness 3.75, Elements {'Ca': 14.31, 'La': 1.94, 'Ce': 1.14, 'Pr': 1.15, 'Sm': 1.58, 'Gd': 0.92, 'Dy': 0.38, 'Y': 1.56, 'Ho': 0.19, 'Al': 6.39, 'H': 2.79, 'S': 3.78, 'Nd': 5.38, 'O': 29.54, 'F': 28.94}
	Found duplicates of "Ciprianiite", with these properties :
			Density None, Hardness None, Elements {'Li': 0.03, 'Ca': 17.86, 'RE': 10.18, 'Th': 14.16, 'Mg': 0.12, 'U': 0.69, 'Ti': 0.46, 'Be': 0.72, 'Al': 1.25, 'Fe': 2.06, 'Si': 10.91, 'B': 4.19, 'H': 0.05, 'O': 36.43, 'F': 0.9}
			Density None, Hardness None, Elements {'Li': 0.03, 'Ca': 17.86, 'RE': 10.18, 'Th': 14.16, 'Mg': 0.12, 'U': 0.69, 'Ti': 0.46, 'Be': 0.72, 'Al': 1.25, 'Fe': 2.06, 'Si': 10.91, 'B': 4.19, 'H': 0.05, 'O': 36.43, 'F': 0.9}
	Found duplicates of "Clearcreekite", with these properties :
			Density None, Hardness None, Elements {'Hg': 83.72, 'H': 0.74, 'C': 1.73, 'O': 13.82}
			Density None, Hardness None, Elements {'Hg': 83.72, 'H': 0.74, 'C': 1.73, 'O': 13.82}
	Found duplicates of "Clerite", with these properties :
			Density 4.66, Hardness 3.75, Elements {'Mn': 12.88, 'Sb': 57.07, 'S': 30.06}
			Density 4.66, Hardness 3.75, Elements {'Mn': 12.88, 'Sb': 57.07, 'S': 30.06}
	Found duplicates of "Cleusonite", with these properties :
			Density 4.74, Hardness 6.5, Elements {'Sr': 0.51, 'U': 12.47, 'Ti': 27.15, 'Mn': 0.21, 'Al': 0.09, 'V': 0.47, 'Zn': 0.28, 'Fe': 20.61, 'H': 0.13, 'Pb': 8.86, 'O': 29.22}
			Density 4.74, Hardness 6.5, Elements {'Sr': 0.51, 'U': 12.47, 'Ti': 27.15, 'Mn': 0.21, 'Al': 0.09, 'V': 0.47, 'Zn': 0.28, 'Fe': 20.61, 'H': 0.13, 'Pb': 8.86, 'O': 29.22}
	Found duplicates of "Clinoatacamite", with these properties :
			Density 3.71, Hardness 3.0, Elements {'Cu': 59.51, 'H': 1.42, 'Cl': 16.6, 'O': 22.47}
			Density 3.71, Hardness 3.0, Elements {'Cu': 59.51, 'H': 1.42, 'Cl': 16.6, 'O': 22.47}
	Found duplicates of "Clinobarylite", with these properties :
			Density 3.97, Hardness 6.5, Elements {'Ba': 43.21, 'Be': 5.42, 'Si': 17.16, 'O': 34.21}
			Density 3.97, Hardness 6.5, Elements {'Ba': 43.21, 'Be': 5.42, 'Si': 17.16, 'O': 34.21}
	Found duplicates of "Clinocervantite", with these properties :
			Density None, Hardness None, Elements {'Sb': 79.19, 'O': 20.81}
			Density None, Hardness None, Elements {'Sb': 79.19, 'O': 20.81}
	Found duplicates of "Clinoferroholmquistite", with these properties :
			Density None, Hardness 5.5, Elements {'Li': 1.7, 'Mg': 2.98, 'Al': 6.63, 'Fe': 13.71, 'Si': 27.58, 'H': 0.25, 'O': 47.14}
			Density None, Hardness 5.5, Elements {'Li': 1.7, 'Mg': 2.98, 'Al': 6.63, 'Fe': 13.71, 'Si': 27.58, 'H': 0.25, 'O': 47.14}
	Found duplicates of "Clinoferrosilite", with these properties :
			Density 4.068, Hardness 5.5, Elements {'Mg': 4.9, 'Fe': 33.77, 'Si': 22.64, 'O': 38.69}
			Density 4.068, Hardness 5.5, Elements {'Mg': 4.9, 'Fe': 33.77, 'Si': 22.64, 'O': 38.69}
	Found duplicates of "Clinoptilolite-K", with these properties :
			Density 2.15, Hardness 3.75, Elements {'K': 6.52, 'Na': 0.69, 'Sr': 1.15, 'Ca': 0.06, 'Mg': 0.16, 'Mn': 0.02, 'Al': 6.22, 'Fe': 0.06, 'Si': 29.15, 'H': 1.71, 'O': 54.27}
			Density 2.15, Hardness 3.75, Elements {'K': 6.52, 'Na': 0.69, 'Sr': 1.15, 'Ca': 0.06, 'Mg': 0.16, 'Mn': 0.02, 'Al': 6.22, 'Fe': 0.06, 'Si': 29.15, 'H': 1.71, 'O': 54.27}
	Found duplicates of "Cloncurryite", with these properties :
			Density None, Hardness 2.0, Elements {'Al': 14.49, 'V': 6.02, 'Cu': 9.55, 'P': 16.63, 'H': 1.42, 'O': 48.06, 'F': 3.83}
			Density None, Hardness 2.0, Elements {'Al': 14.49, 'V': 6.02, 'Cu': 9.55, 'P': 16.63, 'H': 1.42, 'O': 48.06, 'F': 3.83}
	Found duplicates of "Cobaltzippeite", with these properties :
			Density 4.3, Hardness 5.25, Elements {'U': 57.48, 'Co': 4.74, 'H': 1.7, 'S': 3.87, 'O': 32.2}
			Density 4.3, Hardness 5.25, Elements {'U': 57.48, 'Co': 4.74, 'H': 1.7, 'S': 3.87, 'O': 32.2}
	Found duplicates of "Erythrite", with these properties :
			Density 3.12, Hardness 1.75, Elements {'Co': 29.53, 'As': 25.03, 'H': 2.69, 'O': 42.75}
			Density 3.12, Hardness 1.75, Elements {'Co': 29.53, 'As': 25.03, 'H': 2.69, 'O': 42.75}
	Found duplicates of "Cobaltarthurite", with these properties :
			Density 3.22, Hardness 3.75, Elements {'Mg': 0.46, 'Mn': 1.03, 'Fe': 21.98, 'Co': 5.52, 'As': 28.09, 'H': 1.83, 'O': 41.09}
			Density 3.22, Hardness 3.75, Elements {'Mg': 0.46, 'Mn': 1.03, 'Fe': 21.98, 'Co': 5.52, 'As': 28.09, 'H': 1.83, 'O': 41.09}
	Found duplicates of "Dayingite", with these properties :
			Density None, Hardness 5.0, Elements {'Co': 13.22, 'Cu': 14.25, 'Pt': 43.76, 'S': 28.77}
			Density None, Hardness 5.0, Elements {'Co': 13.22, 'Cu': 14.25, 'Pt': 43.76, 'S': 28.77}
	Found duplicates of "Cobaltkieserite", with these properties :
			Density None, Hardness 2.5, Elements {'Co': 33.0, 'Si': 0.16, 'As': 2.57, 'H': 1.15, 'S': 17.41, 'O': 45.71}
			Density None, Hardness 2.5, Elements {'Co': 33.0, 'Si': 0.16, 'As': 2.57, 'H': 1.15, 'S': 17.41, 'O': 45.71}
	Found duplicates of "Cobaltkoritnigite", with these properties :
			Density None, Hardness 2.5, Elements {'Zn': 7.48, 'Co': 20.23, 'As': 34.29, 'H': 1.38, 'O': 36.61}
			Density None, Hardness 2.5, Elements {'Zn': 7.48, 'Co': 20.23, 'As': 34.29, 'H': 1.38, 'O': 36.61}
	Found duplicates of "Cobaltlotharmeyerite", with these properties :
			Density None, Hardness 4.5, Elements {'Ca': 8.55, 'Fe': 8.34, 'Co': 12.57, 'Ni': 3.76, 'As': 31.96, 'H': 0.71, 'O': 34.12}
			Density None, Hardness 4.5, Elements {'Ca': 8.55, 'Fe': 8.34, 'Co': 12.57, 'Ni': 3.76, 'As': 31.96, 'H': 0.71, 'O': 34.12}
	Found duplicates of "Cobaltneustadtelite", with these properties :
			Density None, Hardness 4.5, Elements {'Fe': 7.64, 'Co': 4.03, 'Ni': 1.34, 'Bi': 47.67, 'As': 17.09, 'H': 0.33, 'O': 21.9}
			Density None, Hardness 4.5, Elements {'Fe': 7.64, 'Co': 4.03, 'Ni': 1.34, 'Bi': 47.67, 'As': 17.09, 'H': 0.33, 'O': 21.9}
	Found duplicates of "Cobaltpentlandite", with these properties :
			Density None, Hardness 4.5, Elements {'Co': 67.4, 'S': 32.6}
			Density None, Hardness 4.5, Elements {'Co': 67.4, 'S': 32.6}
	Found duplicates of "Cobalttsumcorite", with these properties :
			Density None, Hardness 4.5, Elements {'Fe': 6.43, 'Co': 7.76, 'Ni': 3.86, 'As': 24.66, 'H': 0.53, 'Pb': 30.69, 'O': 26.07}
			Density None, Hardness 4.5, Elements {'Fe': 6.43, 'Co': 7.76, 'Ni': 3.86, 'As': 24.66, 'H': 0.53, 'Pb': 30.69, 'O': 26.07}
	Found duplicates of "Coiraite", with these properties :
			Density None, Hardness None, Elements {'Fe': 1.44, 'Ag': 0.33, 'Sn': 17.29, 'As': 5.26, 'Pb': 54.57, 'S': 21.1}
			Density None, Hardness None, Elements {'Fe': 1.44, 'Ag': 0.33, 'Sn': 17.29, 'As': 5.26, 'Pb': 54.57, 'S': 21.1}
	Found duplicates of "Coparsite", with these properties :
			Density None, Hardness None, Elements {'V': 4.56, 'Cu': 55.5, 'As': 10.07, 'Cl': 8.73, 'O': 21.14}
			Density None, Hardness None, Elements {'V': 4.56, 'Cu': 55.5, 'As': 10.07, 'Cl': 8.73, 'O': 21.14}
			Density None, Hardness None, Elements {'V': 4.56, 'Cu': 55.5, 'As': 10.07, 'Cl': 8.73, 'O': 21.14}
	Found duplicates of "Cornwallite", with these properties :
			Density None, Hardness None, Elements {'Cu': 46.61, 'As': 21.98, 'H': 0.89, 'O': 30.51}
			Density None, Hardness None, Elements {'Cu': 46.61, 'As': 21.98, 'H': 0.89, 'O': 30.51}
	Found duplicates of "Coskrenite-Ce", with these properties :
			Density None, Hardness None, Elements {'La': 5.9, 'Ce': 21.81, 'H': 2.28, 'C': 3.4, 'S': 9.08, 'Nd': 12.25, 'O': 45.28}
			Density None, Hardness None, Elements {'La': 5.9, 'Ce': 21.81, 'H': 2.28, 'C': 3.4, 'S': 9.08, 'Nd': 12.25, 'O': 45.28}
	Found duplicates of "Coutinhoite", with these properties :
			Density None, Hardness 1.5, Elements {'K': 0.13, 'Ba': 1.23, 'Ca': 0.08, 'Th': 3.29, 'U': 67.4, 'Si': 6.52, 'P': 0.12, 'H': 0.27, 'O': 20.97}
			Density None, Hardness 1.5, Elements {'K': 0.13, 'Ba': 1.23, 'Ca': 0.08, 'Th': 3.29, 'U': 67.4, 'Si': 6.52, 'P': 0.12, 'H': 0.27, 'O': 20.97}
	Found duplicates of "Covellite", with these properties :
			Density 4.68, Hardness 1.75, Elements {'Cu': 66.46, 'S': 33.54}
			Density 4.68, Hardness 1.75, Elements {'Cu': 66.46, 'S': 33.54}
	Found duplicates of "Crandallite", with these properties :
			Density 2.84, Hardness 4.0, Elements {'Ca': 9.68, 'Al': 19.55, 'P': 14.96, 'H': 1.7, 'O': 54.1}
			Density 2.84, Hardness 4.0, Elements {'Ca': 9.68, 'Al': 19.55, 'P': 14.96, 'H': 1.7, 'O': 54.1}
	Found duplicates of "Crawfordite", with these properties :
			Density 3.06, Hardness 3.0, Elements {'Na': 22.14, 'Sr': 28.12, 'P': 9.94, 'C': 3.85, 'O': 35.95}
			Density None, Hardness None, Elements {'Na': 22.14, 'Sr': 28.12, 'P': 9.94, 'C': 3.85, 'O': 35.95}
	Found duplicates of "Crerarite", with these properties :
			Density None, Hardness 3.0, Elements {'Bi': 65.37, 'Pt': 15.26, 'Pb': 5.4, 'Se': 4.94, 'S': 9.03}
			Density None, Hardness 3.0, Elements {'Bi': 65.37, 'Pt': 15.26, 'Pb': 5.4, 'Se': 4.94, 'S': 9.03}
	Found duplicates of "Cronusite", with these properties :
			Density 2.51, Hardness 1.5, Elements {'Ca': 4.95, 'Cr': 32.1, 'H': 2.61, 'S': 39.59, 'O': 20.74}
			Density 2.51, Hardness 1.5, Elements {'Ca': 4.95, 'Cr': 32.1, 'H': 2.61, 'S': 39.59, 'O': 20.74}
	Found duplicates of "Cuboargyrite", with these properties :
			Density 5.33, Hardness 3.0, Elements {'Ag': 41.22, 'Sb': 46.53, 'S': 12.25}
			Density 5.33, Hardness 3.0, Elements {'Ag': 41.22, 'Sb': 46.53, 'S': 12.25}
	Found duplicates of "Cumengite", with these properties :
			Density 4.67, Hardness 2.5, Elements {'Cu': 16.09, 'H': 0.66, 'Pb': 55.08, 'Cl': 18.85, 'O': 9.32}
			Density 4.67, Hardness 2.5, Elements {'Cu': 16.09, 'H': 0.66, 'Pb': 55.08, 'Cl': 18.85, 'O': 9.32}
	Found duplicates of "Faustite", with these properties :
			Density 2.92, Hardness 5.5, Elements {'Al': 19.65, 'Zn': 6.35, 'Cu': 1.54, 'P': 15.04, 'H': 2.08, 'O': 55.34}
			Density 2.92, Hardness 5.5, Elements {'Al': 19.65, 'Zn': 6.35, 'Cu': 1.54, 'P': 15.04, 'H': 2.08, 'O': 55.34}
	Found duplicates of "Cupromakovickyite", with these properties :
			Density None, Hardness None, Elements {'Cu': 7.68, 'Ag': 4.02, 'Bi': 60.14, 'Te': 0.77, 'Pb': 9.48, 'Se': 0.31, 'S': 17.6}
			Density None, Hardness None, Elements {'Cu': 7.68, 'Ag': 4.02, 'Bi': 60.14, 'Te': 0.77, 'Pb': 9.48, 'Se': 0.31, 'S': 17.6}
	Found duplicates of "Cuspidine", with these properties :
			Density 2.84, Hardness 5.5, Elements {'Ca': 43.86, 'Si': 15.37, 'H': 0.14, 'O': 32.83, 'F': 7.8}
			Density 2.84, Hardness 5.5, Elements {'Ca': 43.86, 'Si': 15.37, 'H': 0.14, 'O': 32.83, 'F': 7.8}
	Found duplicates of "Dachiardite-Ca", with these properties :
			Density 2.17, Hardness 4.25, Elements {'Cs': 0.82, 'K': 2.01, 'Ba': 0.08, 'Na': 0.54, 'Sr': 0.59, 'Ca': 3.44, 'Al': 7.31, 'Fe': 0.06, 'Si': 29.7, 'H': 1.41, 'O': 54.04}
			Density 2.17, Hardness 4.25, Elements {'Cs': 0.82, 'K': 2.01, 'Ba': 0.08, 'Na': 0.54, 'Sr': 0.59, 'Ca': 3.44, 'Al': 7.31, 'Fe': 0.06, 'Si': 29.7, 'H': 1.41, 'O': 54.04}
	Found duplicates of "Damiaoite", with these properties :
			Density 10.95, Hardness 5.0, Elements {'In': 54.07, 'Pt': 45.93}
			Density 10.95, Hardness 5.0, Elements {'In': 54.07, 'Pt': 45.93}
	Found duplicates of "Dashkovaite", with these properties :
			Density None, Hardness 1.0, Elements {'Mg': 14.09, 'H': 3.33, 'C': 27.85, 'O': 54.73}
			Density None, Hardness 1.0, Elements {'Mg': 14.09, 'H': 3.33, 'C': 27.85, 'O': 54.73}
	Found duplicates of "Decrespignyite-Y", with these properties :
			Density 3.64, Hardness 4.0, Elements {'Ca': 0.32, 'La': 0.32, 'Pr': 0.16, 'Sm': 0.87, 'Gd': 3.99, 'Dy': 3.18, 'Y': 32.08, 'Ho': 2.28, 'Er': 2.12, 'Tb': 0.37, 'Cu': 8.42, 'H': 1.17, 'C': 5.22, 'Nd': 1.0, 'Cl': 2.9, 'O': 35.6}
			Density 3.64, Hardness 4.0, Elements {'Ca': 0.32, 'La': 0.32, 'Pr': 0.16, 'Sm': 0.87, 'Gd': 3.99, 'Dy': 3.18, 'Y': 32.08, 'Ho': 2.28, 'Er': 2.12, 'Tb': 0.37, 'Cu': 8.42, 'H': 1.17, 'C': 5.22, 'Nd': 1.0, 'Cl': 2.9, 'O': 35.6}
	Found duplicates of "Deliensite", with these properties :
			Density 3.268, Hardness 2.0, Elements {'U': 54.34, 'Fe': 6.37, 'H': 0.92, 'S': 7.32, 'O': 31.05}
			Density 3.268, Hardness 2.0, Elements {'U': 54.34, 'Fe': 6.37, 'H': 0.92, 'S': 7.32, 'O': 31.05}
	Found duplicates of "Dellaventuraite", with these properties :
			Density None, Hardness 5.0, Elements {'K': 1.82, 'Na': 6.2, 'Li': 0.73, 'Ca': 1.35, 'Mg': 5.12, 'Ti': 3.34, 'Mn': 5.43, 'Al': 0.44, 'Zn': 0.08, 'Fe': 4.61, 'Si': 26.0, 'Ni': 0.14, 'H': 0.09, 'O': 44.66}
			Density None, Hardness 5.0, Elements {'K': 1.82, 'Na': 6.2, 'Li': 0.73, 'Ca': 1.35, 'Mg': 5.12, 'Ti': 3.34, 'Mn': 5.43, 'Al': 0.44, 'Zn': 0.08, 'Fe': 4.61, 'Si': 26.0, 'Ni': 0.14, 'H': 0.09, 'O': 44.66}
	Found duplicates of "Deloneite-Ce", with these properties :
			Density 3.93, Hardness 5.0, Elements {'Na': 3.62, 'Sr': 13.8, 'Ca': 12.63, 'Ce': 22.07, 'P': 14.64, 'O': 30.24, 'F': 2.99}
			Density 3.93, Hardness 5.0, Elements {'Na': 3.62, 'Sr': 13.8, 'Ca': 12.63, 'Ce': 22.07, 'P': 14.64, 'O': 30.24, 'F': 2.99}
	Found duplicates of "Demartinite", with these properties :
			Density 2.85, Hardness None, Elements {'K': 35.5, 'Na': 0.21, 'Si': 12.62, 'F': 51.67}
			Density 2.85, Hardness None, Elements {'K': 35.5, 'Na': 0.21, 'Si': 12.62, 'F': 51.67}
	Found duplicates of "Demicheleite-Br", with these properties :
			Density None, Hardness None, Elements {'Bi': 67.51, 'S': 10.15, 'I': 0.83, 'Br': 17.47, 'Cl': 4.05}
			Density None, Hardness None, Elements {'Bi': 67.51, 'S': 10.15, 'I': 0.83, 'Br': 17.47, 'Cl': 4.05}
	Found duplicates of "Dessauite", with these properties :
			Density 4.68, Hardness 6.75, Elements {'Sr': 3.54, 'Y': 3.35, 'U': 3.85, 'Ti': 38.68, 'Fe': 15.04, 'Pb': 2.79, 'O': 32.75}
			Density 4.68, Hardness 6.75, Elements {'Sr': 3.54, 'Y': 3.35, 'U': 3.85, 'Ti': 38.68, 'Fe': 15.04, 'Pb': 2.79, 'O': 32.75}
	Found duplicates of "Diadochite", with these properties :
			Density 2.2, Hardness 3.25, Elements {'Fe': 26.11, 'P': 7.24, 'H': 3.06, 'S': 7.5, 'O': 56.1}
			Density 2.2, Hardness 3.25, Elements {'Fe': 26.11, 'P': 7.24, 'H': 3.06, 'S': 7.5, 'O': 56.1}
	Found duplicates of "Dickinsonite-KMnNa", with these properties :
			Density None, Hardness None, Elements {'K': 0.92, 'Ba': 0.06, 'Na': 6.27, 'Sr': 0.21, 'Li': 0.1, 'Ca': 0.97, 'Mg': 0.07, 'Ti': 0.02, 'Mn': 25.16, 'Al': 1.16, 'Zn': 0.03, 'Fe': 10.05, 'Si': 0.01, 'P': 17.58, 'H': 0.1, 'Pb': 0.1, 'O': 37.15, 'F': 0.03}
			Density None, Hardness None, Elements {'K': 0.92, 'Ba': 0.06, 'Na': 6.27, 'Sr': 0.21, 'Li': 0.1, 'Ca': 0.97, 'Mg': 0.07, 'Ti': 0.02, 'Mn': 25.16, 'Al': 1.16, 'Zn': 0.03, 'Fe': 10.05, 'Si': 0.01, 'P': 17.58, 'H': 0.1, 'Pb': 0.1, 'O': 37.15, 'F': 0.03}
	Found duplicates of "Dickthomssenite", with these properties :
			Density 2.02, Hardness 2.5, Elements {'Mg': 6.98, 'V': 29.25, 'H': 4.05, 'O': 59.72}
			Density 2.02, Hardness 2.5, Elements {'Mg': 6.98, 'V': 29.25, 'H': 4.05, 'O': 59.72}
	Found duplicates of "Diopside", with these properties :
			Density 3.4, Hardness 6.0, Elements {'Ca': 18.51, 'Mg': 11.22, 'Si': 25.94, 'O': 44.33}
			Density 3.4, Hardness 6.0, Elements {'Ca': 18.51, 'Mg': 11.22, 'Si': 25.94, 'O': 44.33}
	Found duplicates of "Dingdaohengite-Ce", with these properties :
			Density 4.83, Hardness 6.0, Elements {'Ca': 1.55, 'La': 16.72, 'Ce': 24.11, 'Th': 0.19, 'Mg': 0.81, 'Ti': 10.99, 'Nb': 0.3, 'Al': 0.02, 'Fe': 7.81, 'Si': 9.08, 'O': 28.44}
			Density 4.83, Hardness 6.0, Elements {'Ca': 1.55, 'La': 16.72, 'Ce': 24.11, 'Th': 0.19, 'Mg': 0.81, 'Ti': 10.99, 'Nb': 0.3, 'Al': 0.02, 'Fe': 7.81, 'Si': 9.08, 'O': 28.44}
	Found duplicates of "Direnzoite", with these properties :
			Density 2.12, Hardness 4.5, Elements {'K': 4.02, 'Ba': 0.03, 'Na': 0.56, 'Sr': 0.06, 'Ca': 1.89, 'Mg': 0.82, 'Al': 7.86, 'Fe': 0.18, 'Si': 28.7, 'H': 1.58, 'O': 54.29}
			Density 2.12, Hardness 4.5, Elements {'K': 4.02, 'Ba': 0.03, 'Na': 0.56, 'Sr': 0.06, 'Ca': 1.89, 'Mg': 0.82, 'Al': 7.86, 'Fe': 0.18, 'Si': 28.7, 'H': 1.58, 'O': 54.29}
	Found duplicates of "Dissakisite-La", with these properties :
			Density 3.79, Hardness 6.75, Elements {'Na': 0.01, 'Sr': 0.16, 'Ca': 8.69, 'La': 7.94, 'Ce': 6.66, 'Pr': 0.49, 'Sm': 0.05, 'Gd': 0.03, 'Er': 0.03, 'Th': 3.79, 'Mg': 2.74, 'Sc': 0.02, 'U': 0.13, 'Ti': 0.26, 'Mn': 0.09, 'Al': 9.0, 'V': 0.07, 'Zn': 0.18, 'Cr': 1.4, 'Ga': 0.01, 'Fe': 4.08, 'Si': 15.13, 'Ni': 0.11, 'P': 0.04, 'H': 0.18, 'Nd': 0.99, 'O': 37.7, 'F': 0.03}
			Density 3.79, Hardness 6.75, Elements {'Na': 0.01, 'Sr': 0.16, 'Ca': 8.69, 'La': 7.94, 'Ce': 6.66, 'Pr': 0.49, 'Sm': 0.05, 'Gd': 0.03, 'Er': 0.03, 'Th': 3.79, 'Mg': 2.74, 'Sc': 0.02, 'U': 0.13, 'Ti': 0.26, 'Mn': 0.09, 'Al': 9.0, 'V': 0.07, 'Zn': 0.18, 'Cr': 1.4, 'Ga': 0.01, 'Fe': 4.08, 'Si': 15.13, 'Ni': 0.11, 'P': 0.04, 'H': 0.18, 'Nd': 0.99, 'O': 37.7, 'F': 0.03}
	Found duplicates of "Diversilite-Ce", with these properties :
			Density 3.68, Hardness 5.0, Elements {'K': 4.77, 'Ba': 21.99, 'Na': 2.02, 'Sr': 0.35, 'Ca': 0.07, 'La': 4.1, 'Ce': 5.67, 'Pr': 0.62, 'Sm': 0.13, 'Ti': 5.44, 'Mn': 0.82, 'Nb': 1.1, 'Fe': 2.38, 'Si': 14.98, 'H': 0.69, 'Nd': 1.02, 'O': 33.85}
			Density 3.68, Hardness 5.0, Elements {'K': 4.77, 'Ba': 21.99, 'Na': 2.02, 'Sr': 0.35, 'Ca': 0.07, 'La': 4.1, 'Ce': 5.67, 'Pr': 0.62, 'Sm': 0.13, 'Ti': 5.44, 'Mn': 0.82, 'Nb': 1.1, 'Fe': 2.38, 'Si': 14.98, 'H': 0.69, 'Nd': 1.02, 'O': 33.85}
	Found duplicates of "Dmitryivanovite", with these properties :
			Density None, Hardness None, Elements {'Ca': 25.36, 'Ti': 0.06, 'Al': 34.03, 'Si': 0.05, 'O': 40.5}
			Density None, Hardness None, Elements {'Ca': 25.36, 'Ti': 0.06, 'Al': 34.03, 'Si': 0.05, 'O': 40.5}
	Found duplicates of "Dorallcharite", with these properties :
			Density 3.85, Hardness 3.5, Elements {'K': 1.9, 'Tl': 23.21, 'Fe': 27.18, 'H': 0.98, 'S': 10.4, 'O': 36.33}
			Density 3.85, Hardness 3.5, Elements {'K': 1.9, 'Tl': 23.21, 'Fe': 27.18, 'H': 0.98, 'S': 10.4, 'O': 36.33}
	Found duplicates of "Dovyrenite", with these properties :
			Density None, Hardness None, Elements {'Ca': 31.57, 'Hf': 0.1, 'Mg': 0.08, 'Zr': 12.4, 'Ti': 0.03, 'Mn': 0.02, 'Nb': 0.03, 'Fe': 0.21, 'Si': 15.42, 'H': 0.61, 'O': 39.53}
			Density None, Hardness None, Elements {'Ca': 31.57, 'Hf': 0.1, 'Mg': 0.08, 'Zr': 12.4, 'Ti': 0.03, 'Mn': 0.02, 'Nb': 0.03, 'Fe': 0.21, 'Si': 15.42, 'H': 0.61, 'O': 39.53}
	Found duplicates of "Dozyite", with these properties :
			Density 2.66, Hardness 2.5, Elements {'Mg': 20.39, 'Al': 12.93, 'Si': 13.46, 'H': 1.45, 'O': 51.77}
			Density 2.66, Hardness 2.5, Elements {'Mg': 20.39, 'Al': 12.93, 'Si': 13.46, 'H': 1.45, 'O': 51.77}
	Found duplicates of "Dualite", with these properties :
			Density 2.84, Hardness 5.0, Elements {'K': 0.07, 'Ba': 0.23, 'Na': 13.2, 'Sr': 1.16, 'Ca': 5.76, 'RE': 3.07, 'Zr': 4.0, 'Ti': 2.65, 'Mn': 2.0, 'Nb': 1.36, 'Al': 0.1, 'Fe': 0.82, 'Si': 24.03, 'H': 0.16, 'Cl': 0.58, 'O': 40.81}
			Density 2.84, Hardness 5.0, Elements {'K': 0.07, 'Ba': 0.23, 'Na': 13.2, 'Sr': 1.16, 'Ca': 5.76, 'RE': 3.07, 'Zr': 4.0, 'Ti': 2.65, 'Mn': 2.0, 'Nb': 1.36, 'Al': 0.1, 'Fe': 0.82, 'Si': 24.03, 'H': 0.16, 'Cl': 0.58, 'O': 40.81}
	Found duplicates of "Dukeite", with these properties :
			Density None, Hardness 3.5, Elements {'Cr': 6.4, 'Bi': 77.17, 'H': 0.19, 'O': 16.25}
			Density None, Hardness 3.5, Elements {'Cr': 6.4, 'Bi': 77.17, 'H': 0.19, 'O': 16.25}
	Found duplicates of "Emmonsite", with these properties :
			Density 4.52, Hardness 2.25, Elements {'Fe': 16.56, 'Te': 56.75, 'H': 0.6, 'O': 26.09}
			Density 4.52, Hardness 2.25, Elements {'Fe': 16.56, 'Te': 56.75, 'H': 0.6, 'O': 26.09}
	Found duplicates of "Dusmatovite", with these properties :
			Density 2.96, Hardness 4.5, Elements {'K': 5.32, 'Na': 0.59, 'Li': 0.44, 'Y': 4.53, 'Zr': 1.55, 'Mn': 5.6, 'Zn': 12.51, 'Si': 28.65, 'O': 40.8}
			Density 2.96, Hardness 4.5, Elements {'K': 5.32, 'Na': 0.59, 'Li': 0.44, 'Y': 4.53, 'Zr': 1.55, 'Mn': 5.6, 'Zn': 12.51, 'Si': 28.65, 'O': 40.8}
	Found duplicates of "Dzharkenite", with these properties :
			Density 7.34, Hardness 5.0, Elements {'Fe': 26.13, 'Se': 73.87}
			Density 7.34, Hardness 5.0, Elements {'Fe': 26.13, 'Se': 73.87}
	Found duplicates of "Edgarite", with these properties :
			Density None, Hardness 2.25, Elements {'Nb': 42.81, 'Fe': 12.87, 'S': 44.33}
			Density None, Hardness 2.25, Elements {'Nb': 42.81, 'Fe': 12.87, 'S': 44.33}
	Found duplicates of "Effenbergerite", with these properties :
			Density 3.54, Hardness 4.5, Elements {'Ba': 29.02, 'Cu': 13.43, 'Si': 23.74, 'O': 33.81}
			Density 3.54, Hardness 4.5, Elements {'Ba': 29.02, 'Cu': 13.43, 'Si': 23.74, 'O': 33.81}
	Found duplicates of "Eirikite", with these properties :
			Density None, Hardness None, Elements {'K': 2.88, 'Na': 10.15, 'Be': 1.33, 'Al': 5.96, 'Si': 30.99, 'O': 45.91, 'F': 2.8}
			Density None, Hardness None, Elements {'K': 2.88, 'Na': 10.15, 'Be': 1.33, 'Al': 5.96, 'Si': 30.99, 'O': 45.91, 'F': 2.8}
	Found duplicates of "Ekatite", with these properties :
			Density None, Hardness 3.0, Elements {'Zn': 2.97, 'Fe': 35.51, 'Si': 0.96, 'As': 32.33, 'H': 0.35, 'O': 27.89}
			Density None, Hardness 3.0, Elements {'Zn': 2.97, 'Fe': 35.51, 'Si': 0.96, 'As': 32.33, 'H': 0.35, 'O': 27.89}
	Found duplicates of "Elsmoreite", with these properties :
			Density None, Hardness 3.0, Elements {'H': 0.37, 'W': 76.68, 'O': 22.95}
			Density None, Hardness 3.0, Elements {'H': 0.37, 'W': 76.68, 'O': 22.95}
	Found duplicates of "Emilite", with these properties :
			Density None, Hardness 3.75, Elements {'Cu': 7.49, 'Bi': 50.03, 'Pb': 25.08, 'S': 17.39}
			Density None, Hardness 3.75, Elements {'Cu': 7.49, 'Bi': 50.03, 'Pb': 25.08, 'S': 17.39}
	Found duplicates of "Epidote-Sr", with these properties :
			Density None, Hardness None, Elements {'Sr': 16.51, 'Ca': 7.55, 'Al': 10.17, 'Fe': 10.52, 'Si': 15.87, 'H': 0.19, 'O': 39.19}
			Density None, Hardness None, Elements {'Sr': 16.51, 'Ca': 7.55, 'Al': 10.17, 'Fe': 10.52, 'Si': 15.87, 'H': 0.19, 'O': 39.19}
	Found duplicates of "Ercitite", with these properties :
			Density None, Hardness 3.5, Elements {'Na': 9.06, 'Ca': 0.71, 'Mn': 12.89, 'Fe': 11.38, 'P': 13.85, 'H': 2.23, 'O': 49.88}
			Density None, Hardness 3.5, Elements {'Na': 9.06, 'Ca': 0.71, 'Mn': 12.89, 'Fe': 11.38, 'P': 13.85, 'H': 2.23, 'O': 49.88}
	Found duplicates of "Ernienickelite", with these properties :
			Density 3.84, Hardness 2.0, Elements {'Mn': 42.31, 'Ni': 15.07, 'H': 1.55, 'O': 41.07}
			Density 3.84, Hardness 2.0, Elements {'Mn': 42.31, 'Ni': 15.07, 'H': 1.55, 'O': 41.07}
	Found duplicates of "Esperanzaite", with these properties :
			Density 3.24, Hardness 4.5, Elements {'Na': 4.08, 'Ca': 14.21, 'Al': 9.57, 'As': 26.57, 'H': 0.89, 'O': 31.21, 'F': 13.47}
			Density 3.24, Hardness 4.5, Elements {'Na': 4.08, 'Ca': 14.21, 'Al': 9.57, 'As': 26.57, 'H': 0.89, 'O': 31.21, 'F': 13.47}
	Found duplicates of "Eulytite", with these properties :
			Density 6.6, Hardness 4.5, Elements {'Si': 7.58, 'Bi': 75.16, 'O': 17.26}
			Density 6.6, Hardness 4.5, Elements {'Si': 7.58, 'Bi': 75.16, 'O': 17.26}
	Found duplicates of "Eveslogite", with these properties :
			Density 2.85, Hardness 5.0, Elements {'K': 7.05, 'Rb': 0.18, 'Ba': 2.52, 'Na': 3.48, 'Sr': 2.32, 'Ca': 13.24, 'Zr': 0.25, 'Ta': 0.21, 'Ti': 3.89, 'Mn': 0.77, 'Nb': 4.56, 'Al': 0.16, 'Fe': 0.85, 'Si': 19.54, 'H': 0.32, 'Cl': 0.42, 'O': 37.53, 'F': 2.71}
			Density 2.85, Hardness 5.0, Elements {'K': 7.05, 'Rb': 0.18, 'Ba': 2.52, 'Na': 3.48, 'Sr': 2.32, 'Ca': 13.24, 'Zr': 0.25, 'Ta': 0.21, 'Ti': 3.89, 'Mn': 0.77, 'Nb': 4.56, 'Al': 0.16, 'Fe': 0.85, 'Si': 19.54, 'H': 0.32, 'Cl': 0.42, 'O': 37.53, 'F': 2.71}
	Found duplicates of "Eyselite", with these properties :
			Density None, Hardness None, Elements {'Ga': 0.69, 'Fe': 12.94, 'Ge': 53.91, 'H': 0.29, 'O': 32.17}
			Density None, Hardness None, Elements {'Ga': 0.69, 'Fe': 12.94, 'Ge': 53.91, 'H': 0.29, 'O': 32.17}
	Found duplicates of "Faizievite", with these properties :
			Density None, Hardness None, Elements {'K': 3.23, 'Rb': 0.11, 'Ba': 0.23, 'Na': 1.48, 'Sr': 0.62, 'Li': 1.75, 'Ca': 10.35, 'Ti': 8.03, 'Nb': 0.08, 'Si': 28.28, 'O': 44.54, 'F': 1.3}
			Density None, Hardness None, Elements {'K': 3.23, 'Rb': 0.11, 'Ba': 0.23, 'Na': 1.48, 'Sr': 0.62, 'Li': 1.75, 'Ca': 10.35, 'Ti': 8.03, 'Nb': 0.08, 'Si': 28.28, 'O': 44.54, 'F': 1.3}
	Found duplicates of "Fangite", with these properties :
			Density 6.185, Hardness 2.25, Elements {'Tl': 75.11, 'As': 9.18, 'S': 15.71}
			Density 6.185, Hardness 2.25, Elements {'Tl': 75.11, 'As': 9.18, 'S': 15.71}
	Found duplicates of "Farneseite", with these properties :
			Density None, Hardness None, Elements {'K': 4.44, 'Na': 10.37, 'Ca': 4.34, 'Al': 13.87, 'Si': 14.78, 'H': 0.08, 'S': 4.54, 'Cl': 4.6, 'O': 42.94, 'F': 0.04}
			Density None, Hardness None, Elements {'K': 4.44, 'Na': 10.37, 'Ca': 4.34, 'Al': 13.87, 'Si': 14.78, 'H': 0.08, 'S': 4.54, 'Cl': 4.6, 'O': 42.94, 'F': 0.04}
	Found duplicates of "Faujasite-Na", with these properties :
			Density 1.93, Hardness 5.0, Elements {'Na': 2.29, 'Ca': 1.99, 'Mg': 0.4, 'Al': 8.95, 'Si': 22.63, 'H': 3.06, 'O': 60.67}
			Density 1.93, Hardness 5.0, Elements {'Na': 2.29, 'Ca': 1.99, 'Mg': 0.4, 'Al': 8.95, 'Si': 22.63, 'H': 3.06, 'O': 60.67}
	Found duplicates of "Feinglosite", with these properties :
			Density 6.52, Hardness 4.5, Elements {'Zn': 6.52, 'Fe': 1.86, 'As': 14.95, 'H': 0.27, 'Pb': 55.12, 'S': 2.13, 'O': 19.15}
			Density 6.52, Hardness 4.5, Elements {'Zn': 6.52, 'Fe': 1.86, 'As': 14.95, 'H': 0.27, 'Pb': 55.12, 'S': 2.13, 'O': 19.15}
	Found duplicates of "Feklichevite", with these properties :
			Density 2.87, Hardness 5.5, Elements {'Na': 8.42, 'Sr': 0.23, 'Ca': 11.01, 'La': 0.09, 'Ce': 0.14, 'Hf': 0.53, 'Zr': 8.56, 'Ti': 0.08, 'Mn': 0.38, 'Nb': 1.68, 'Fe': 3.82, 'Si': 23.33, 'H': 0.2, 'Cl': 0.61, 'O': 40.8, 'F': 0.12}
			Density 2.87, Hardness 5.5, Elements {'Na': 8.42, 'Sr': 0.23, 'Ca': 11.01, 'La': 0.09, 'Ce': 0.14, 'Hf': 0.53, 'Zr': 8.56, 'Ti': 0.08, 'Mn': 0.38, 'Nb': 1.68, 'Fe': 3.82, 'Si': 23.33, 'H': 0.2, 'Cl': 0.61, 'O': 40.8, 'F': 0.12}
	Found duplicates of "Felbertalite", with these properties :
			Density None, Hardness 3.75, Elements {'Cd': 0.31, 'Cu': 3.53, 'Ag': 1.2, 'Bi': 48.19, 'Pb': 29.93, 'S': 16.84}
			Density None, Hardness 3.75, Elements {'Cd': 0.31, 'Cu': 3.53, 'Ag': 1.2, 'Bi': 48.19, 'Pb': 29.93, 'S': 16.84}
	Found duplicates of "Fencooperite", with these properties :
			Density None, Hardness 4.75, Elements {'Ba': 44.99, 'Al': 0.75, 'Fe': 8.99, 'Si': 12.63, 'H': 0.11, 'C': 1.33, 'Cl': 3.15, 'O': 28.05}
			Density None, Hardness 4.75, Elements {'Ba': 44.99, 'Al': 0.75, 'Fe': 8.99, 'Si': 12.63, 'H': 0.11, 'C': 1.33, 'Cl': 3.15, 'O': 28.05}
	Found duplicates of "Ferri-clinoferroholmquistite", with these properties :
			Density None, Hardness None, Elements {'Na': 0.98, 'Li': 1.78, 'Mg': 3.04, 'Al': 0.83, 'Fe': 21.27, 'Si': 26.49, 'H': 0.21, 'O': 44.87, 'F': 0.54}
			Density None, Hardness None, Elements {'Na': 0.98, 'Li': 1.78, 'Mg': 3.04, 'Al': 0.83, 'Fe': 21.27, 'Si': 26.49, 'H': 0.21, 'O': 44.87, 'F': 0.54}
	Found duplicates of "Ferri-ottoliniite", with these properties :
			Density None, Hardness 5.5, Elements {'K': 0.32, 'Na': 2.87, 'Li': 1.32, 'Ca': 0.28, 'Mg': 3.79, 'Ti': 0.33, 'Mn': 0.83, 'Al': 0.31, 'Zn': 2.34, 'Fe': 16.97, 'Si': 25.96, 'H': 0.18, 'O': 43.48, 'F': 1.03}
			Density None, Hardness 5.5, Elements {'K': 0.32, 'Na': 2.87, 'Li': 1.32, 'Ca': 0.28, 'Mg': 3.79, 'Ti': 0.33, 'Mn': 0.83, 'Al': 0.31, 'Zn': 2.34, 'Fe': 16.97, 'Si': 25.96, 'H': 0.18, 'O': 43.48, 'F': 1.03}
			Density None, Hardness 5.5, Elements {'K': 0.32, 'Na': 2.87, 'Li': 1.32, 'Ca': 0.28, 'Mg': 3.79, 'Ti': 0.33, 'Mn': 0.83, 'Al': 0.31, 'Zn': 2.34, 'Fe': 16.97, 'Si': 25.96, 'H': 0.18, 'O': 43.48, 'F': 1.03}
	Found duplicates of "Ferrierite-Na", with these properties :
			Density 2.14, Hardness 3.25, Elements {'K': 1.45, 'Ba': 0.11, 'Na': 2.7, 'Sr': 0.1, 'Ca': 0.08, 'Mg': 0.35, 'Al': 5.18, 'Si': 33.4, 'H': 1.39, 'O': 55.24}
			Density 2.14, Hardness 3.25, Elements {'K': 1.45, 'Ba': 0.11, 'Na': 2.7, 'Sr': 0.1, 'Ca': 0.08, 'Mg': 0.35, 'Al': 5.18, 'Si': 33.4, 'H': 1.39, 'O': 55.24}
	Found duplicates of "Ferripedrizite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 0.19, 'Na': 2.11, 'Li': 1.94, 'Ca': 0.24, 'Mg': 4.31, 'Ti': 0.51, 'Mn': 0.46, 'Al': 0.68, 'Zn': 0.08, 'Fe': 16.57, 'Si': 26.77, 'H': 0.16, 'O': 44.43, 'F': 1.56}
			Density None, Hardness 6.0, Elements {'K': 0.19, 'Na': 2.11, 'Li': 1.94, 'Ca': 0.24, 'Mg': 4.31, 'Ti': 0.51, 'Mn': 0.46, 'Al': 0.68, 'Zn': 0.08, 'Fe': 16.57, 'Si': 26.77, 'H': 0.16, 'O': 44.43, 'F': 1.56}
	Found duplicates of "Ferriwhittakerite", with these properties :
			Density None, Hardness 6.0, Elements {'K': 0.63, 'Na': 5.09, 'Li': 1.08, 'Ca': 0.51, 'Mg': 4.12, 'Ti': 0.66, 'Mn': 0.76, 'Al': 0.31, 'Zn': 3.01, 'Fe': 13.26, 'Si': 25.89, 'H': 0.15, 'O': 42.96, 'F': 1.58}
			Density None, Hardness 6.0, Elements {'K': 0.63, 'Na': 5.09, 'Li': 1.08, 'Ca': 0.51, 'Mg': 4.12, 'Ti': 0.66, 'Mn': 0.76, 'Al': 0.31, 'Zn': 3.01, 'Fe': 13.26, 'Si': 25.89, 'H': 0.15, 'O': 42.96, 'F': 1.58}
	Found duplicates of "Ferriwinchite", with these properties :
			Density None, Hardness 5.5, Elements {'K': 0.42, 'Na': 3.89, 'Ca': 3.7, 'Mg': 8.65, 'Ti': 0.06, 'Mn': 0.58, 'Al': 0.61, 'Fe': 12.48, 'Si': 25.94, 'H': 0.09, 'O': 43.04, 'F': 0.56}
			Density None, Hardness 5.5, Elements {'K': 0.42, 'Na': 3.89, 'Ca': 3.7, 'Mg': 8.65, 'Ti': 0.06, 'Mn': 0.58, 'Al': 0.61, 'Fe': 12.48, 'Si': 25.94, 'H': 0.09, 'O': 43.04, 'F': 0.56}
	Found duplicates of "Ferroaluminoceladonite", with these properties :
			Density 2.93, Hardness 2.25, Elements {'K': 9.13, 'Al': 6.3, 'Fe': 13.04, 'Si': 26.23, 'H': 0.47, 'O': 44.83}
			Density 2.93, Hardness 2.25, Elements {'K': 9.13, 'Al': 6.3, 'Fe': 13.04, 'Si': 26.23, 'H': 0.47, 'O': 44.83}
			Density 2.93, Hardness 2.25, Elements {'K': 9.13, 'Al': 6.3, 'Fe': 13.04, 'Si': 26.23, 'H': 0.47, 'O': 44.83}
	Found duplicates of "Ferroceladonite", with these properties :
			Density 3.05, Hardness 2.25, Elements {'K': 9.74, 'Fe': 13.92, 'Si': 27.99, 'H': 0.5, 'O': 47.84}
			Density 3.05, Hardness 2.25, Elements {'K': 9.74, 'Fe': 13.92, 'Si': 27.99, 'H': 0.5, 'O': 47.84}
	Found duplicates of "Ferrohogbomite-2N2S", with these properties :
			Density None, Hardness 6.5, Elements {'Mg': 1.99, 'Ti': 3.05, 'Mn': 0.22, 'Al': 31.66, 'Zn': 4.79, 'Cr': 0.08, 'Ga': 0.16, 'Fe': 18.47, 'Si': 0.02, 'Ni': 0.05, 'Sn': 0.28, 'H': 0.16, 'O': 39.06}
			Density None, Hardness 6.5, Elements {'Mg': 1.99, 'Ti': 3.05, 'Mn': 0.22, 'Al': 31.66, 'Zn': 4.79, 'Cr': 0.08, 'Ga': 0.16, 'Fe': 18.47, 'Si': 0.02, 'Ni': 0.05, 'Sn': 0.28, 'H': 0.16, 'O': 39.06}
	Found duplicates of "Ferroholmquistite", with these properties :
			Density None, Hardness 5.5, Elements {'K': 0.05, 'Na': 0.11, 'Li': 1.61, 'Mg': 4.42, 'Mn': 0.14, 'Al': 6.31, 'Fe': 11.81, 'Si': 27.79, 'H': 0.25, 'O': 47.44, 'F': 0.07}
			Density None, Hardness 5.5, Elements {'K': 0.05, 'Na': 0.11, 'Li': 1.61, 'Mg': 4.42, 'Mn': 0.14, 'Al': 6.31, 'Fe': 11.81, 'Si': 27.79, 'H': 0.25, 'O': 47.44, 'F': 0.07}
	Found duplicates of "Ferrokentbrooksite", with these properties :
			Density 3.06, Hardness 5.5, Elements {'K': 0.39, 'Na': 9.28, 'Sr': 0.41, 'Ca': 5.98, 'RE': 4.41, 'Y': 0.47, 'Hf': 0.17, 'Zr': 8.67, 'Ta': 0.11, 'Ti': 0.06, 'Mn': 3.14, 'Nb': 1.84, 'Al': 0.06, 'Fe': 4.13, 'Si': 21.86, 'H': 0.04, 'Cl': 0.98, 'O': 37.13, 'F': 0.89}
			Density 3.06, Hardness 5.5, Elements {'K': 0.39, 'Na': 9.28, 'Sr': 0.41, 'Ca': 5.98, 'RE': 4.41, 'Y': 0.47, 'Hf': 0.17, 'Zr': 8.67, 'Ta': 0.11, 'Ti': 0.06, 'Mn': 3.14, 'Nb': 1.84, 'Al': 0.06, 'Fe': 4.13, 'Si': 21.86, 'H': 0.04, 'Cl': 0.98, 'O': 37.13, 'F': 0.89}
	Found duplicates of "Ferrokinoshitalite", with these properties :
			Density 3.69, Hardness 3.0, Elements {'K': 2.97, 'Ba': 13.05, 'Mg': 4.62, 'Al': 7.69, 'Fe': 21.22, 'Si': 13.34, 'H': 0.26, 'O': 34.5, 'F': 2.35}
			Density 3.69, Hardness 3.0, Elements {'K': 2.97, 'Ba': 13.05, 'Mg': 4.62, 'Al': 7.69, 'Fe': 21.22, 'Si': 13.34, 'H': 0.26, 'O': 34.5, 'F': 2.35}
	Found duplicates of "Ferronordite-Ce", with these properties :
			Density 3.5, Hardness 5.25, Elements {'Na': 8.7, 'Sr': 11.05, 'Ce': 17.67, 'Fe': 7.04, 'Si': 21.25, 'O': 34.3}
			Density 3.5, Hardness 5.25, Elements {'Na': 8.7, 'Sr': 11.05, 'Ce': 17.67, 'Fe': 7.04, 'Si': 21.25, 'O': 34.3}
	Found duplicates of "Ferronordite-La", with these properties :
			Density 3.54, Hardness 5.0, Elements {'Ba': 0.34, 'Na': 8.41, 'Sr': 10.83, 'Ca': 0.4, 'La': 9.71, 'Ce': 7.0, 'Pr': 0.88, 'Mg': 0.18, 'Mn': 1.92, 'Al': 0.07, 'Zn': 1.88, 'Fe': 2.93, 'Si': 20.76, 'Nd': 0.72, 'O': 33.96}
			Density 3.54, Hardness 5.0, Elements {'Ba': 0.34, 'Na': 8.41, 'Sr': 10.83, 'Ca': 0.4, 'La': 9.71, 'Ce': 7.0, 'Pr': 0.88, 'Mg': 0.18, 'Mn': 1.92, 'Al': 0.07, 'Zn': 1.88, 'Fe': 2.93, 'Si': 20.76, 'Nd': 0.72, 'O': 33.96}
	Found duplicates of "Ferrorhodsite", with these properties :
			Density 5.71, Hardness 4.5, Elements {'Fe': 7.62, 'Cu': 6.39, 'Ir': 10.58, 'Pt': 2.33, 'Rh': 42.37, 'S': 30.7}
			Density 5.71, Hardness 4.5, Elements {'Fe': 7.62, 'Cu': 6.39, 'Ir': 10.58, 'Pt': 2.33, 'Rh': 42.37, 'S': 30.7}
	Found duplicates of "Ferrorosemaryite", with these properties :
			Density None, Hardness 4.0, Elements {'Na': 2.12, 'Ca': 0.35, 'Mg': 0.11, 'Mn': 6.28, 'Al': 4.87, 'Fe': 23.59, 'P': 20.44, 'O': 42.24}
			Density None, Hardness 4.0, Elements {'Na': 2.12, 'Ca': 0.35, 'Mg': 0.11, 'Mn': 6.28, 'Al': 4.87, 'Fe': 23.59, 'P': 20.44, 'O': 42.24}
	Found duplicates of "Ferrosaponite", with these properties :
			Density 2.49, Hardness 2.0, Elements {'K': 0.07, 'Na': 0.17, 'Ca': 2.36, 'Mg': 3.92, 'Al': 5.18, 'Fe': 22.38, 'Si': 15.31, 'H': 1.98, 'O': 48.62}
			Density 2.49, Hardness 2.0, Elements {'K': 0.07, 'Na': 0.17, 'Ca': 2.36, 'Mg': 3.92, 'Al': 5.18, 'Fe': 22.38, 'Si': 15.31, 'H': 1.98, 'O': 48.62}
	Found duplicates of "Ferroskutterudite", with these properties :
			Density None, Hardness 6.25, Elements {'Fe': 12.1, 'Co': 8.38, 'Ni': 0.04, 'As': 78.13, 'S': 1.34}
			Density None, Hardness 6.25, Elements {'Fe': 12.1, 'Co': 8.38, 'Ni': 0.04, 'As': 78.13, 'S': 1.34}
	Found duplicates of "Ferrotitanowodginite", with these properties :
			Density None, Hardness 5.5, Elements {'Ta': 60.96, 'Ti': 8.07, 'Fe': 9.41, 'O': 21.56}
			Density None, Hardness 5.5, Elements {'Ta': 60.96, 'Ti': 8.07, 'Fe': 9.41, 'O': 21.56}
	Found duplicates of "Fianelite", with these properties :
			Density 3.21, Hardness 3.0, Elements {'Mn': 30.04, 'V': 24.37, 'As': 5.12, 'H': 1.1, 'O': 39.37}
			Density 3.21, Hardness 3.0, Elements {'Mn': 30.04, 'V': 24.37, 'As': 5.12, 'H': 1.1, 'O': 39.37}
	Found duplicates of "Filatovite", with these properties :
			Density None, Hardness 5.5, Elements {'K': 10.58, 'Na': 0.47, 'Al': 14.36, 'Zn': 3.08, 'Fe': 0.16, 'Cu': 0.75, 'Si': 5.78, 'As': 26.44, 'P': 0.73, 'O': 37.64}
			Density None, Hardness 5.5, Elements {'K': 10.58, 'Na': 0.47, 'Al': 14.36, 'Zn': 3.08, 'Fe': 0.16, 'Cu': 0.75, 'Si': 5.78, 'As': 26.44, 'P': 0.73, 'O': 37.64}
	Found duplicates of "Florenskyite", with these properties :
			Density None, Hardness None, Elements {'Ti': 30.36, 'Fe': 40.83, 'Ni': 5.69, 'P': 23.11}
			Density None, Hardness None, Elements {'Ti': 30.36, 'Fe': 40.83, 'Ni': 5.69, 'P': 23.11}
	Found duplicates of "Fluocerite-La", with these properties :
			Density 5.93, Hardness 4.5, Elements {'La': 63.78, 'Ce': 7.15, 'F': 29.08}
			Density 5.93, Hardness 4.5, Elements {'La': 63.78, 'Ce': 7.15, 'F': 29.08}
	Found duplicates of "Fluorannite", with these properties :
			Density 3.17, Hardness 3.0, Elements {'K': 7.43, 'Li': 0.29, 'Mg': 1.03, 'Al': 7.97, 'Fe': 27.11, 'Si': 16.6, 'H': 0.11, 'O': 35.46, 'F': 4.01}
			Density 3.17, Hardness 3.0, Elements {'K': 7.43, 'Li': 0.29, 'Mg': 1.03, 'Al': 7.97, 'Fe': 27.11, 'Si': 16.6, 'H': 0.11, 'O': 35.46, 'F': 4.01}
	Press any key to continue . . .
"""
