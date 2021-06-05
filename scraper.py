import csv, operator, re
from dataclasses import dataclass
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException

link = "http://webmineral.com/data/index.html"
firstMineral = 4 # 4
lastMineral = 14 # 6982

mineralSelector = lambda i: f"body > table > tbody > tr:nth-child({i}) > td:nth-child(2) > a"
nameSelector = "#header > tbody > tr > td > center > table:nth-child(6) > tbody > tr:nth-child(1) > td > h3 > b"
densityXPath = "//a[@href=\"../help/Density.shtml\"]/../../td[2]"
hardnessXPath = "//a[@href=\"../help/Hardness.shtml\"]/../../td[2]"
elementXPath = "//a[@href=\"../help/Composition.shtml\"]/../../../tr[4]/td[2]/font"
"""
Composition row :			//*[@id="header"]/tbody/tr/td/center/table[3]/tbody/tr[3]
First element row :			//*[@id="header"]/tbody/tr/td/center/table[3]/tbody/tr[4]
First element and oxide :	//*[@id="header"]/tbody/tr/td/center/table[3]/tbody/tr[4]/td[2]/font
First element and oxide :	//*[@id="header"]/tbody/tr/td/center/table[3]/tbody/tr[4]/td[2]/font/text()[2]
"""
#	Check with "https://regexr.com/"
namePattern = "(General )(.*)( Information)"	# Match group 2
densityPattern = ".*Average = (\d*[.]?\d+)"		# Match group 1
hardnessPattern = "(\d.*\d|\d)( - )"			# Match group 1
elementPattern = "(\d*\.?\d*)"					# Match group 1
#	In case of a hardness range value, takes the average as the hardness
hardnessSeparator = '-'

# options = Options()
options = webdriver.chrome.options.Options()
options.headless = False
options.page_load_strategy = 'none'
options.add_argument('log-level=3') # Log levels : INFO = 0, WARNING = 1, LOG_ERROR = 2, LOG_FATAL = 3. Default is 0
services = webdriver.chrome.service.Service(executable_path = "bin/chromedriver.exe")

@dataclass
class Mineral:
	name: str = None
	density: float = None
	hardness: float = None
	elements: dict = None

	#	Functions to check for duplicates (based on names)
	def __eq__(self, other):
		return (self.name == other.name)
	def __hash__(self):
		return hash(('name', self.name))

minerals = []
with webdriver.Chrome(options = options, service = services) as driver:
	wait = WebDriverWait(driver, 15)
	driver.get(link)
	mainTab = driver.current_window_handle

	for i in range(firstMineral, lastMineral + 1):
		#	Get mineral link
		wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, mineralSelector(i))))
		mineralLink = driver.find_element(By.CSS_SELECTOR, mineralSelector(i)).get_attribute('href')

		#	Open mineral in new tab
		driver.switch_to.new_window('tab')
		driver.get(mineralLink)
		#	Wait until elements are present, then write values into minerals[]
		wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, nameSelector)))
		name = driver.find_element(By.CSS_SELECTOR, nameSelector).text
		#	Extract name from title
		m = re.search(namePattern, name)
		minerals.append(Mineral(name = m.group(2)))

		try: #	Density
			temp = driver.find_element(By.XPATH, densityXPath).text
			minerals[i - firstMineral].density = float(temp)
		except NoSuchElementException: pass
		except ValueError:
			m = re.search(densityPattern, temp)
			minerals[i - firstMineral].density = float(m.group(1))

		try: #	Hardness
			temp = driver.find_element(By.XPATH, hardnessXPath).text
			minerals[i - firstMineral].hardness = float(temp)
		except NoSuchElementException: pass
		except ValueError:
			m = re.search(hardnessPattern, temp)
			temp = m.group(1)
			try:
				minerals[i - firstMineral].hardness = float(temp)
			except ValueError:
				temp = list(map(float, temp.split(hardnessSeparator)))
				minerals[i - firstMineral].hardness = sum(temp)/len(temp)

		temp = driver.find_element(By.XPATH, elementXPath).text
		m = re.search(elementPattern, temp)
		temp = m.group(1)

		try: #	Elements
			temp = driver.find_element(By.XPATH, elementXPath).text
			minerals[i - firstMineral].hardness = float(temp)
		except NoSuchElementException: pass
		except ValueError:
			m = re.search(hardnessPattern, temp)
			temp = m.group(1)
			try:
				minerals[i - firstMineral].hardness = float(temp)
			except ValueError:
				temp = list(map(float, temp.split(hardnessSeparator)))
				minerals[i - firstMineral].hardness = sum(temp)/len(temp)


		#	Close current tab and go back to the main one
		driver.close()
		driver.switch_to.window(mainTab)

#	Removes duplicates and returns a new list
minerals = list(set(minerals))
minerals.sort(key = operator.attrgetter('name'))
for mineral in minerals:
	print(f"Name : {mineral.name}, Density : {mineral.density}, Hardness : {mineral.hardness}")
