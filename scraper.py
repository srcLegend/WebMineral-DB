import csv
import operator
import re
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from multiprocessing import Lock, Process, Queue
from time import time

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

def generate_headers(headers, periodic_table):
	""" Appends and returns a given headers list with all elements from the periodic table\n
		Requires a periodic table in CSV format, where headers are in row 1\\
		Columns must be: Atomic Number, Name, Symbol, Mass, etc..."""

	with open(periodic_table) as file:
		rows = csv.reader(file)
		whitespace = re.compile(r'\s*')
		for row in rows:
			if (rows.line_num == 1):
				continue
			headers.append(re.sub(whitespace, '', row[2]))

def generate_minerals(links, baselinks, patterns, queue, lock):
	"""Generates mineral objects. Seems to be thread-safe\n
	   Needs list/dictionaries of links and dictionaries of search patterns,\\
		along with a queue and a lock object"""

	minerals, skipped = [], []
	for link in links:
		start_timer = time()

		r = requests.get(link)
		s = BeautifulSoup(r.content, 'html.parser')

		try: # to extract mineral name
			content = s.select('h3 > b')[0].contents[0]
			m = patterns['name'].search(content)
			name = m.group(1).replace('(', '').replace(')', '').strip()
			if patterns['exclude'].search(name):
				skipped.append(link)
				continue
			mineral = Mineral(name = name)
		except IndexError: # Couldn't find given CSS Selectors
			try: # to follow redirects in case it is one, skip otherwise
				if ('redirect' in s.contents[0].text.lower()):
					content = s.contents[0].contents[1].contents[3].attrs['content']
					m = patterns['link'].search(content)
					links.append(m.group(1))
				else:
					skipped.append(link)
			except AttributeError: # Link broken
				skipped.append(link)
			finally:
				continue
		except AttributeError: # Name pattern did not fit
			skipped.append(link)
			continue

		# Check for elements
		if (elements_tag := s.select(f"a[href*=\"{baselinks['elements']}\"]")):
			lines = list(list(list(elements_tag[0].parents)[0].parents)[0].next_siblings)
			# Look for elements in non-empty lines
			for line in (line for line in lines if (line != '\n')):
				line = ' '.join(line.text.split())
				if (patterns['elements_done'] in line):
					break
				elif (m := patterns['element'].search(line)):
					element, percentage = m.group(2), m.group(1)
					# Convert rare earth element oxides into pure elements
					if (element == 'RE'):
						# TODO
						pass
					# If element is already present, sum percentages
					if (element in mineral.elements):
						mineral.elements[element] += float(percentage)
					else:
						mineral.elements[element] = float(percentage)

		# Check for density
		if (density_tag := s.select(f"a[href*=\"{baselinks['density']}\"]")):
			density = list(list(density_tag[0].parents)[0].parent)[3].contents[0]
			try: # to store density as a float
				mineral.density = float(density)
			except ValueError: # Extract float value if direct conversion failed
				m = patterns['density'].search(density)
				mineral.density = float(m.group(1))

		# Check for hardness
		if (hardness_tag := s.select(f"a[href*=\"{baselinks['hardness']}\"]")):
			hardness = list(list(hardness_tag[0].parents)[0].parent)[3].contents[0]
			try: # to store hardness as a float
				mineral.hardness = float(hardness)
			except ValueError: # Extract float value(s) if direct conversion failed
				m = patterns['hardness'].search(hardness)
				# If single value, take it as hardness, otherwise average the range
				if (not m.group(2)):
					mineral.hardness = float(m.group(1))
				else:
					mineral.hardness = (float(m.group(1)) + float(m.group(2)))/2

		minerals.append(mineral)
		end_timer = time()
		# Lock printing for proper console output
		with lock:
			print(f"Done downloading {mineral.name} in {end_timer - start_timer:.2f} seconds")

	# Put gathered data in queue to be taken by the main thread
	queue.put((minerals, skipped))

def generate_links(baselinks, datafiles, queue, lock):
	"""Gathers links of all available minerals, then writes them into files\\
	   Needs dictionaries of links, datafiles, along with a queue and a lock object"""

	r = requests.get(baselinks['data'] + 'index.html')
	s = BeautifulSoup(r.content, 'html.parser')

	links, skipped = [], []
	lines = s.contents[2].contents[3].contents[3].contents
	del r, s
	# Look for links in non-empty lines
	for line in [line for line in lines if (line != '\n')][3:-1]:
		link = line.contents[2].contents[0].attrs['href']
		if (not '.shtml' in link):
			skipped.append(baselinks['data'] + link)
			continue
		links.append(baselinks['data'] + link)

	with lock:
		try: # to write files
			with open(datafiles['mineral_links'], 'w') as file:
				for link in links:
					file.write(link + '\n')

			# Put gathered links in queue to be taken by the main thread
			queue.put((links, skipped))
		except Exception as e: # Unable to write files for any reason
			queue.put(e)

def get_minerals(baselinks, datafiles, patterns, settings):
	"""Splits links, which will be taken from files if present, or from a website otherwise,\\
		into batches for threading\n
	   Needs dictionaries of links, search patterns and settings"""

	lock = Lock()
	link_queue, mineral_queue = Queue(), Queue()

	compare_links = False
	try: # to read files
		with lock:
			with open(datafiles['mineral_links']) as file:
				links = [link.strip() for link in file.readlines()]

			with open(datafiles['skipped_links']) as file:
				skipped = [link.strip() for link in file.readlines()]

			# Regenerate links to be checked for new ones later
			link_process = Process(target = generate_links, args = (baselinks, datafiles, link_queue, lock))
			link_process.start()
			compare_links = True
	except FileNotFoundError:
		# Generate and wait for links to be ready before continuing
		link_process = Process(target = generate_links, args = (baselinks, datafiles, link_queue, lock))
		link_process.start()
		l_queue = link_queue.get()
		link_process.join()
		if (type(l_queue) == tuple):
			links, skipped = l_queue
		else:
			raise l_queue

	# Separate ready links into batches for multithreading, then start processes
	max_links = len(links)//settings['threads']
	remaining_links = len(links)%settings['threads']
	slicers, processes = [0, 0], []
	for t in range(0, settings['threads']):
		slicers = [slicers[1], (t + 1)*max_links]
		if (remaining_links > 0):
			remaining_links -= 1
			slicers[1] += 1

		if ((t + 1) < settings['threads']):
			processes.append(Process(target = generate_minerals,
									args = (links[slicers[0]:slicers[1]], baselinks, patterns, mineral_queue, lock)))
		else:
			processes.append(Process(target = generate_minerals,
									args = (links[slicers[0]:], baselinks, patterns, mineral_queue, lock)))
		processes[-1].start()

	# Check for new links, starts a new process if any
	if compare_links:
		l_queue = link_queue.get()
		link_process.join()
		if (type(l_queue) == tuple):
			new_links = [link for link in l_queue[0] if link not in set(links)]
			if new_links:
				processes.append(Process(target = generate_minerals,
										 args = (new_links, baselinks, patterns, mineral_queue, lock)))
				processes[-1].start()
			new_skipped = [link for link in l_queue[1] if link not in set(skipped)]
			if new_skipped:
				skipped.extend(new_skipped)
		else:
			raise l_queue

	# Wait for process completion
	minerals = []
	for _ in processes:
		m_queue = mineral_queue.get()
		minerals.extend(m_queue[0])
		skipped.extend(m_queue[1])

	for p in processes:
		p.join()

	return minerals, skipped

if (__name__ == '__main__'):
	# Whether to regenerate minerals database or not
	generate = True
	# Whether to overwrite certain minerals with custom values or not
	custom = True

	datafiles = {'minerals_database'  : "data/MineralsDatabase.csv",
				 'current_minerals'	  : "data/CurrentMinerals.csv",
				 'custom_minerals'	  : "data/CustomMinerals.csv",
				 'periodic_table'	  : "data/PeriodicTable.csv",
				 'rare_earth_minerals': "data/RareEarthMinerals.txt",
				 'mineral_links'	  : "data/MineralLinks.txt",
				 'skipped_links'	  : "data/SkippedLinks.txt"}

	# CSV initial headers
	headers = ["Mineral", "Density", "Hardness"]
	generate_headers(headers, datafiles['periodic_table'])

	if generate:
		settings = {'timeout' : 30, 'threads' : 8}
		baselinks = {'data'	   : "http://webmineral.com/data/",
					 'elements': "../help/Composition.shtml",
					 'density' : "../help/Density.shtml",
					 'hardness': "../help/Hardness.shtml"}

		# RegEx patterns. Check with "https://regexr.com/"
		patterns = {'name'	  : re.compile("General\s*(.*)\s*Information"),	 # Match group 1
					'link'	  : re.compile("(http.*)"),						 # Match group 1
					'exclude' : re.compile("(IMA\S*)"),						 # Match group 1
					'element' : re.compile("^\D+(\d+\.?\d*)\s*%\s*(\w+).*"), # Match group 1 for percentage, group 2 for element
					'density' : re.compile("(\d+\.?\d*)\s*$"),				 # Match group 1
					'hardness': re.compile("(\d+\.?\d*)-?(\d+\.?\d*)?"),	 # Match group 1, test group 2 for averaging
					'elements_done': "Empirical Formula"}					 # End of elements block

		minerals, skipped = get_minerals(baselinks, datafiles, patterns, settings)

		# Removes duplicates and returns a new sorted list
		minerals = list(set(minerals))
		minerals.sort(key = operator.attrgetter('name'))
		skipped.sort()

		# Writes everything to a CSV file
		# Additionally keep track of minerals containing rare earth elements
		rare_earth_minerals = []
		with open(datafiles['minerals_database'], 'w', newline = '') as file:
			rows = csv.DictWriter(file, fieldnames = headers)
			rows.writeheader()
			for mineral in minerals:
				tempdict = {headers[0]:	mineral.name,
							headers[1]:	mineral.density,
							headers[2]: mineral.hardness}
				# Write elements with a precision of two decimals
				tempdict.update({k: f"{v:.2f}" for k, v in mineral.elements.items()})
				# Delete rare earth elements until implementation
				if ('RE' in tempdict):
					del tempdict['RE']
					rare_earth_minerals.append(mineral)
				rows.writerow(tempdict)

		# Write skipped links
		if skipped:
			with open(datafiles['skipped_links'], 'w') as file:
				for link in skipped:
					file.write(link + '\n')

		# Write rare earth minerals
		if rare_earth_minerals:
			with open(datafiles['rare_earth_minerals'], 'w') as file:
				for mineral in rare_earth_minerals:
					file.write(mineral.name + '\n')

	if custom:
		# Read minerals off of database if a new one isn't generated
		if not generate:
			minerals = []
			with open(datafiles['minerals_database']) as file:
				rows = csv.DictReader(file, fieldnames = headers)
				for row in rows:
					# Skip header row
					if (rows.line_num == 1):
						continue
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
		with open(datafiles['custom_minerals']) as file:
			rows = csv.DictReader(file, fieldnames = headers)
			for row in rows:
				# Skip header row
				if (rows.line_num == 1):
					continue

				# Check if a custom mineral is already listed in the database
				# Append relevant list accordingly, then delete it
				index = next((index for (index, mineral) in enumerate(minerals) if (mineral.name == row[headers[0]])), None)
				if index:
					modified.append(minerals[index])
					del minerals[index]
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
		with open(datafiles['current_minerals'], 'w', newline = '') as file:
			rows = csv.DictWriter(file, fieldnames = headers)
			rows.writeheader()
			for mineral in minerals:
				tempdict = {headers[0]:	mineral.name,
							headers[1]:	mineral.density,
							headers[2]: mineral.hardness}
				# Write elements with a precision of two decimals
				tempdict.update({k: f"{v:.2f}" for k, v in mineral.elements.items()})
				# Delete rare earth elements until implementation
				if ('RE' in tempdict):
					del tempdict['RE']
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
